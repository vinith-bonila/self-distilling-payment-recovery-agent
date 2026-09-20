# Prompt changelog

All prompts are versioned files (`<name>.<version>.md`) with a front-matter
header stating purpose, inputs and expected output shape. No prompt strings live
inline in Python — code loads prompts by name and version.

## agent_decider

### v1 — 2026-09-21 (Phase 5)
Initial system prompt for the hand-written agent loop. Establishes the strict
single-JSON-object response contract (a tool call or a final decision), forbids
inventing tools or emitting code/rules, and states that effect tools are
guardrailed. No measured effect yet — the offline stub does not consume this
text (it reads the structured JSON state directly); this prompt is the contract
the Groq client will use. Baseline behaviour is established in Phase 6.

## rule_proposer

### v1 — 2026-09-21 (Phase 7)
System prompt for the distillation proposer. Asks the model to turn a cluster of
successful trajectories into ONE rule in the closed grammar, using only the
injected schema, and treats the output as untrusted (validated by the grammar,
rejected wholesale on any violation). The offline stub returns the `suggested`
grammar proposal deterministically; the prompt is the contract a real Groq model
would author against. Measured effect: in the Phase 7 re-run this drove LLM
traffic share down while recovery held within overlapping Wilson intervals (see
evals/results.md).
