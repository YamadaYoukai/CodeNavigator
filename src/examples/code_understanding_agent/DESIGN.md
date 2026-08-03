# Code-understanding agent boundaries

This package provides a small, in-memory event boundary for the agent harness
and dependency-injected boundaries for context construction, model decisions,
and its two code-retrieval tools. `TraceRecorder` owns `task_id` and continuous
sequence assignment. Most event timing remains caller-supplied;
`TracedModelClient` is the one boundary that measures its wrapped model call.

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

Callers may inject a `ToolCallResolver`. Resolution happens before the
executable `ToolCall` is recorded. `RepositoryAliasResolver` uses only exact
configured names: canonical names pass through, configured aliases become one
canonical indexed name, and no case, basename, URL, suffix, or fuzzy fallback
is attempted. The preceding `ModelResult` therefore retains the model's raw
decision while the `ToolCall` records the arguments actually sent to the tool.

## Error classification

Only the following router error codes are emitted in a `ToolResult.error_type`:

| Error code | Condition |
| --- | --- |
| `unknown_tool` | The call's `tool_name` is not one of the two contract names. |
| `unknown_repository` | A model supplied a repository name absent from the configured canonical names and exact aliases. |
| `ambiguous_repository` | One configured alias refers to more than one canonical repository. |
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

### OpenAI adapter and trace wrapper

`OpenAIModel` makes one injected Chat Completions call with `tool_choice="auto"`
and `parallel_tool_calls=False`. Its tool definitions use strict function
calling: every object rejects additional properties and every declared field is
required. Fields that are optional in the router contract remain nullable; the
model must still emit them. Pydantic defaults and titles are removed from the
wire schema.

A response crosses the boundary only when it contains exactly one allowlisted,
validated tool call or JSON content that validates as `FinalAnswerDecision`.
Provider/transport failures become `model_execution_error`; refusals, empty
responses, malformed JSON, multiple or unknown tool calls, and invalid arguments
become `invalid_model_output`. Neither error retains the provider exception or
raw response.

`TracedModelClient` is provider-independent and can wrap both `FakeModel` and
`OpenAIModel`. It generates a local model `request_id`, appends a safe
`ModelRequest`, delegates once, then appends exactly one correlated
`ModelResult`. A successful result has a serialized decision and no error type;
an error result has only its stable error type. Every model result also has a
required non-negative `elapsed_ms`, measured with `time.perf_counter` around
the wrapped `decide` call. This duration includes SDK transport and response
validation for `OpenAIModel`, but excludes context construction and trace
serialization. Tests inject a deterministic clock instead of asserting real
wall-clock timing.

The trace schema has no fields for API keys, request headers, base URLs, raw
exceptions, or raw responses. The real-model smoke report repeats this as an
executable redaction check: it compares the configured API key and base URL
against the serialized trace and rejects forbidden transport/provider field
names before writing evidence.

Here, "safe" is limited to the provider/transport boundary. `ModelInput` and a
validated decision are intentionally retained as task evidence, so this layer
is not a general-purpose source-code or PII redactor. Callers must still apply
their repository and data-classification policy before constructing context.

### Adapter versus API proxy

`OpenAIModel` is an application adapter, not a network proxy. It never reads
credentials or endpoint configuration. The caller creates and injects an
OpenAI SDK client, and therefore owns the API key, timeout, retry policy, and
`base_url`. That URL may select the OpenAI endpoint or an explicitly trusted
OpenAI-compatible gateway. A gateway must support Chat Completions function
tools, strict JSON schemas, `tool_choice`, and `parallel_tool_calls`; transport
compatibility is verified by the opt-in smoke run rather than assumed.

When a private gateway must bypass workstation HTTP, HTTPS, or SOCKS proxies,
the smoke runner's explicit `--no-proxy-base-url` option extracts only the
configured URL hostname and appends it to both process-local `NO_PROXY` and
`no_proxy`. It neither prints the hostname nor changes system proxy settings.
The production caller remains responsible for making the equivalent transport
choice before constructing its SDK client.

Chat Completions is retained here as the narrow compatibility boundary for the
configured gateway. OpenAI recommends the Responses API for new projects, but
migrating API shape and gateway capability is deliberately outside this
adapter-only change. Regardless of transport, neither endpoint/auth metadata
nor provider-native payloads may cross into the trace.

## Context Builder contract

`ContextBuilder.build(ContextState)` is an independently callable pure
transformation. It does not invoke a model, a tool, a retry, a timeout, a loop,
memory, persistence, RAG, or another agent. For the same `ContextState`, it
returns the same `ModelInput` and leaves the supplied state unchanged.

The model-facing payload has this fixed field order:

1. `system_instruction`
2. `current_task`
3. `repository_hints`
4. `tool_schemas`
5. `evidence`
6. `remaining_tool_calls`

Each `repository_hints` item contains one `canonical_name` and zero or more
exact aliases. The model must emit only a matching canonical name. For
`search_code`, it emits `repo=null` when no hint applies rather than inventing
a GitHub owner/repository path. For `get_file_context`, it reuses the canonical
name returned by search or supplied by a hint. Alias resolution is repeated at
the execution boundary as defense in depth.

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
