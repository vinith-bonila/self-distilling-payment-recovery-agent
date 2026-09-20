"""The hand-written agent loop.

Each iteration: build messages (system prompt + JSON state), ask the LLM for a
single JSON decision, and either (a) call a read tool, (b) route an effect tool
through the guarded executor, or (c) finish. Unknown tools and invalid arguments
are rejected safely and fed back as observations; unparseable output fails safe.
The loop is bounded by a max iteration count and a wall-clock timeout, and it
always returns a complete, replayable :class:`Trajectory`.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agentcore.agent.tools import (
    ToolArgError,
    ToolRegistry,
    bound_observation,
    validate_args,
)
from agentcore.guardrails import ExecutionStatus, GuardedExecutor
from agentcore.llm_client import LLMClient, LLMMessage
from agentcore.trajectory import AgentOutcome, Trajectory, TrajectoryStep


@dataclass
class _Decision:
    thought: str
    is_final: bool
    tool: str | None = None
    args: dict[str, Any] | None = None
    resolution: str | None = None


def _parse_decision(text: str) -> _Decision | None:
    """Parse the LLM's response into a decision, or None if malformed."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, Mapping):
        return None
    thought = data.get("thought", "")
    thought = thought if isinstance(thought, str) else ""
    if "final" in data and "action" not in data:
        final = data["final"]
        resolution = None
        if isinstance(final, Mapping):
            res = final.get("resolution")
            resolution = res if isinstance(res, str) else None
        return _Decision(thought=thought, is_final=True, resolution=resolution)
    if "action" in data and "final" not in data:
        action = data["action"]
        if not isinstance(action, Mapping):
            return None
        tool = action.get("tool")
        args = action.get("args", {})
        if not isinstance(tool, str) or not isinstance(args, Mapping):
            return None
        return _Decision(thought=thought, is_final=False, tool=tool, args=dict(args))
    return None


class AgentLoop:
    """Drives one recovery decision to a bounded, recorded conclusion."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        registry: ToolRegistry,
        executor: GuardedExecutor,
        system_prompt: str,
        system_prompt_id: str,
        max_iterations: int = 5,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._executor = executor
        self._system_prompt = system_prompt
        self._system_prompt_id = system_prompt_id
        self._max_iterations = max_iterations
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def executor(self) -> GuardedExecutor:
        return self._executor

    def _messages(
        self, case: Mapping[str, Any], history: list[dict[str, Any]]
    ) -> list[LLMMessage]:
        state = {
            "case": dict(case),
            "tools": self._registry.catalog(),
            "history": history,
        }
        return [
            LLMMessage("system", self._system_prompt),
            LLMMessage("user", json.dumps(state, sort_keys=True)),
        ]

    def run(self, *, subject_id: str, case: Mapping[str, Any]) -> Trajectory:
        trajectory = Trajectory(
            run_id=self._executor.run_id,
            subject_id=subject_id,
            system_prompt_id=self._system_prompt_id,
            started_at=self._now().isoformat(),
        )
        history: list[dict[str, Any]] = []
        resolution_status: ExecutionStatus | None = None
        start = self._clock()

        outcome = AgentOutcome.MAX_ITERATIONS
        for index in range(1, self._max_iterations + 1):
            if self._clock() - start > self._timeout_seconds:
                outcome = AgentOutcome.FAILED_TIMEOUT
                break

            response = self._llm.complete(self._messages(case, history))
            decision = _parse_decision(response.text)

            if decision is None:
                self._record(
                    trajectory, index, "", None, None, "malformed_output",
                    {"raw": response.text}, response,
                )
                outcome = AgentOutcome.FAILED_MALFORMED
                break

            if decision.is_final:
                self._record(
                    trajectory, index, decision.thought, None, None, "final",
                    {"resolution": decision.resolution}, response,
                )
                outcome = self._final_outcome(
                    decision.resolution, resolution_status, trajectory
                )
                break

            tool = self._registry.get(decision.tool or "")
            if tool is None:
                observation = {"error": "unknown_tool", "tool": decision.tool}
                self._record(
                    trajectory, index, decision.thought, decision.tool,
                    decision.args, "rejected_unknown_tool", observation, response,
                )
                history.append(
                    {"tool": decision.tool, "args": decision.args, "observation": observation}
                )
                continue

            try:
                args = validate_args(tool, decision.args or {})
            except ToolArgError as exc:
                observation = {"error": "invalid_arguments", "detail": str(exc)}
                self._record(
                    trajectory, index, decision.thought, decision.tool,
                    decision.args, "rejected_invalid_args", observation, response,
                )
                history.append(
                    {"tool": decision.tool, "args": decision.args, "observation": observation}
                )
                continue

            if tool.is_effect:
                assert tool.build_action is not None
                try:
                    action = tool.build_action(args)
                except Exception as exc:  # noqa: BLE001 — fail safe, keep trajectory
                    observation = {"error": "effect_unavailable", "detail": str(exc)}
                    self._record(
                        trajectory, index, decision.thought, decision.tool, args,
                        "rejected_effect_error", observation, response,
                    )
                    history.append(
                        {"tool": decision.tool, "args": args, "observation": observation}
                    )
                    continue
                exec_result = self._executor.execute(action)
                resolution_status = exec_result.status
                trajectory.resolution_action = action.name
                observation = bound_observation(
                    {
                        "status": exec_result.status.value,
                        "result": exec_result.result,
                        "detail": exec_result.detail,
                    }
                )
                self._record(
                    trajectory, index, decision.thought, decision.tool, args,
                    f"effect_{exec_result.status.value}", observation, response,
                )
            else:
                assert tool.read_handler is not None
                observation = bound_observation(tool.read_handler(args))
                self._record(
                    trajectory, index, decision.thought, decision.tool, args,
                    "tool_call", observation, response,
                )
            history.append({"tool": decision.tool, "args": args, "observation": observation})

        trajectory.outcome = outcome
        trajectory.ended_at = self._now().isoformat()
        return trajectory

    def _final_outcome(
        self,
        resolution: str | None,
        resolution_status: ExecutionStatus | None,
        trajectory: Trajectory,
    ) -> AgentOutcome:
        if resolution_status is ExecutionStatus.PENDING_APPROVAL:
            return AgentOutcome.PENDING_APPROVAL
        if resolution_status in {
            ExecutionStatus.REJECTED_ACTION_BUDGET,
            ExecutionStatus.REJECTED_SPEND_CAP,
            ExecutionStatus.REJECTED_BREAKER_OPEN,
        }:
            return AgentOutcome.REJECTED
        action = trajectory.resolution_action or resolution
        if action == "escalate_to_human":
            return AgentOutcome.ESCALATED
        return AgentOutcome.RESOLVED

    def _record(
        self,
        trajectory: Trajectory,
        index: int,
        thought: str,
        tool: str | None,
        args: dict[str, Any] | None,
        decision: str,
        observation: Any,
        response,
    ) -> None:
        trajectory.steps.append(
            TrajectoryStep(
                index=index,
                thought=thought,
                tool=tool,
                args=args,
                decision=decision,
                observation=observation,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                latency_ms=response.latency_ms,
                cached=response.cached,
            )
        )
