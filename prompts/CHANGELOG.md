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
