# Durable AgentLoop checkpoint resume evidence

- Executed on: `2026-08-23`
- Scenario: synthetic public Click DateTime conversion fixture
- Counts as a final M4 incident case: `false`
- Process A exit code: `75` (intentional interruption)
- Process B exit code: `0`
- Resumable checkpoint SHA-256: `cae98afa691ce6d874d5fb2fced1ba4650901be19411511dbf41265e0715ad03`
- Completed record SHA-256: `19ecf5c0fa34dcba9e0b2a9a7c57ae2465f0064beb5db13af611b916fa7f4079`
- Event sequence before resume: `session -> step -> model_request -> model_result -> tool_call -> tool_result`
- Event sequence after resume: `session -> step -> model_request -> model_result -> tool_call -> tool_result -> step -> model_request -> model_result -> final_answer`
- Tool executions across both processes: `1`
- Final reason: `completed`
- Final evidence: `click/src/click/types.py:491`

## Honest boundary

- No real model or Zoekt service was called.
- Only the boundary after one successful ToolResult is recoverable.
- No concurrent-worker, external-side-effect, or arbitrary-crash exactly-once claim is made.
- This synthetic fixture is recovery evidence, not one of the five final M4 incident cases.
