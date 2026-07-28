# Code-understanding agent trace and tool router

This package provides a small, in-memory event boundary for the agent harness
and a dependency-injected router for its two code-retrieval tools. `TraceRecorder`
owns `task_id` and continuous sequence assignment; callers supply deterministic
`elapsed_ms` values when they need them.

## Router contract

`ToolRouter` supports exactly these tool names:

| Tool | Validated arguments |
| --- | --- |
| `search_code` | `query` (non-empty), optional `repo`, `lang`, and `path`, `limit` 1–100, and `literal` |
| `get_file_context` | non-empty `repository` and `file_path`, positive `line_number`, and `lines_before` / `lines_after` 0–100 |

Unknown argument keys are rejected. The router is constructed with exactly one
adapter for each name. It has no import of FastMCP, an MCP client, Zoekt, or the
file reader; callers inject `ToolDefinition` implementations. `PydanticToolAdapter`
is the default adapter for an ordinary callable plus an argument model, and it
accepts either synchronous or asynchronous callables.

Each `execute(call)` consumes an unrecorded `ToolCall`, records it, and then
records the matching `ToolResult`. The result always retains the original
`call_id`, so a trace has a deterministic `ToolCall → ToolResult` sequence even
for rejected or failed calls.

## Error classification

Only the following router error codes are emitted in a `ToolResult.error_type`:

| Error code | Condition |
| --- | --- |
| `unknown_tool` | The call's `tool_name` is not one of the two contract names. |
| `invalid_arguments` | The injected adapter rejects caller-supplied arguments. |
| `tool_execution_error` | Adapter validation unexpectedly fails, invocation fails, or its result cannot be normalized to a non-null JSON value. |

The trace carries the stable classification rather than backend exception text.
This keeps traces deterministic and avoids exposing implementation details; the
adapter owner remains responsible for diagnostic logging at its integration
boundary.

## Result normalization and deliberate limits

The router converts a Pydantic result model with `model_dump(mode="json")` and
then validates the outcome as JSON before creating a successful `ToolResult`.
This allows domain models such as `CodeSearchResponse` to cross the trace
boundary without coupling the trace schema to those models.

The design intentionally does not retry tools, measure wall-clock time, persist
traces, or provide a plugin registry. Retrying can duplicate side effects and
would obscure the one-call/one-result trace invariant. A fixed two-tool
allowlist is preferable here because the agent workflow is deliberately narrow;
adding another capability requires an explicit contract, adapter, and tests.
