# Code-understanding agent boundaries

This package provides a small, in-memory event boundary for the agent harness
and dependency-injected boundaries for context construction, model decisions,
and its two code-retrieval tools. `TraceRecorder` owns `task_id` and continuous
sequence assignment; callers supply deterministic `elapsed_ms` values when they
need them.

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

## Model boundary responsibilities

`ModelClient` owns only the typed `ModelInput → ModelDecision` handoff. A
decision is either one allowlisted retrieval-tool request or one structured
final answer. For a tool request, `ToolCallDecision` selects the shared
`SearchCodeArguments` or `GetFileContextArguments` contract by `tool_name` and
validates the arguments before the decision can cross the model boundary.
`ToolRouter` defensively repeats the same validation immediately before tool
execution. The model boundary does not build context, execute tools, manage a
loop, enforce budgets, retry, or persist state. `FakeModel` follows the same
interface with a caller-supplied decision script and detached input snapshots,
providing a network-free and clock-free test double.

## Context Builder contract

`ContextBuilder.build(ContextState)` is an independently callable pure
transformation. It does not invoke a model, a tool, a retry, a timeout, a loop,
memory, persistence, RAG, or another agent. For the same `ContextState`, it
returns the same `ModelInput` and leaves the supplied state unchanged.

The model-facing payload has this fixed field order:

1. `system_instruction`
2. `current_task`
3. `tool_schemas`
4. `evidence`
5. `remaining_tool_calls`

`tool_schemas` always contains exactly these two router contracts in this
order: `search_code`, then `get_file_context`. The schemas are derived from
the existing Pydantic argument models and copied for each output, so a caller
cannot alter the Builder's fixed allowlist through a prior result.

`evidence_item_budget` is an input-only count of atomic evidence entries. It
is intentionally not a token estimate and is separate from
`remaining_tool_calls`; constructing context never consumes tool budget.

### Decision: fact-first evidence trimming

**Status:** accepted, 2026-07-29.

When the evidence-item budget cannot fit every entry, the Builder keeps
`fact` entries before `history` entries. Within the same tier, original input
order is retained. Facts are verified observations such as a tool result or
source location; history is prior narration or planning that can be recreated
from the task. This gives the future model the strongest available grounding
while dropping lower-value history first. Whole entries are selected rather
than token-truncated, avoiding tokenizer/model coupling and preserving a
deterministic, inspectable contract.
