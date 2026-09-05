# Code-understanding agent boundaries

This package provides a small event boundary for the agent harness and
dependency-injected boundaries for context construction, model decisions, and
its two code-retrieval tools. `TraceRecorder` remains in memory while a process
is running; the optional checkpoint store persists only the one recovery-safe
prefix described below. `TraceRecorder` owns `task_id` and continuous sequence
assignment. Most event timing remains caller-supplied; `TracedModelClient` is
the one boundary that measures its wrapped model call.

## Agent loop contract

`AgentLoop` is the termination-orchestration layer over the existing
boundaries. Its caller supplies one immutable initial `ContextState`
and injects a `ContextBuilder`, `ModelClient` (normally a
`TracedModelClient`), `ToolStepExecutor`, and the single `TraceRecorder` shared
by the traced model and tool router. The recorder must be empty and
unfinalized when `run` starts. The asynchronous result contains the recorded
`FinalAnswer`, a detached final `ContextState`, and the number of attempted
tool calls; the caller-owned initial state and injected decision objects remain
unchanged.

The per-task tool-call hard limit is six. At startup the loop copies the input
state and sets its effective remaining budget to
`min(initial remaining_tool_calls, 6)`, so a larger caller value cannot weaken
the limit. Each `ToolCallDecision` is delegated once to `ToolStepExecutor`,
which remains the only owner of call construction, execution, evidence
insertion, and budget consumption. The loop also counts attempted calls and
never starts a seventh one. It does not retry, execute tools concurrently, or
copy builder, router, or tool-step logic.

One successful, evidence-backed run has this exact event shape:

```text
Session
  -> Step(n)
  -> ModelRequest(n) -> ModelResult(n)
  -> [ToolCall(n) -> ToolResult(n) -> Step(n+1) -> ...]
  -> FinalAnswer(termination_reason="completed", evidence=[...])
```

The loop appends one `Step` immediately before every model decision. The
existing `TracedModelClient` owns each correlated model event pair, the
existing router owns each correlated tool event pair, and `TraceRecorder`
continues to own `task_id`, continuous sequence assignment, and terminal-state
enforcement. A `FinalAnswerDecision` is copied into one `FinalAnswer`; that
terminal event is last, and no model or tool call is permitted afterward. Its
structured model citations are normalized before the public event is written.

## Durable checkpoint and recovery contract

Persistence is optional and deliberately narrower than the loop itself. With a
`FileCheckpointStore` injected, `AgentLoop` writes a `resumable` record only
after a correlated `ToolCall -> ToolResult(status="success")` has been recorded,
the next state and exact next `ModelInput` have been constructed, and the next
`Step` has not been appended. `on_checkpoint_saved`, when supplied, runs only
after that durable write returns; the offline proof uses it to simulate process
termination without recording any event from the next step.

A schema-version-1 resumable record contains exactly:

- `schema_version=1`, `status="resumable"`, and
  `phase="after_successful_tool_result"`;
- one non-empty `task_id` and a positive, monotonically increasing
  `generation`;
- the complete current `ContextState` and the complete next `ModelInput`;
- attempted Tool count, remaining Tool budget, and next step number;
- the complete `TraceRecorder.to_dict()` event prefix.

The duplicated task, sequence, step, and budget information is intentional. A
load succeeds only when the trace begins with one `Session`, contains complete
and correlated `Step -> ModelRequest -> ModelResult(success) -> ToolCall ->
ToolResult(success)` groups, ends at the successful Tool result, and has no
terminal event. Event task IDs and sequences must be exact and continuous. The
initial request budget must be at most six, each request budget must decrease
with its completed Tool pair, and the saved state, duplicated remaining budget,
Tool count, and next step must agree with that history.

Each saved Tool group also has one semantic correlation rule. The recorded
`ToolCall` must match the preceding `ToolCallDecision` in `call_id`, tool name,
and every argument. The only permitted argument change is the existing
`RepositoryAliasResolver` transformation: `search_code.repo` or
`get_file_context.repository` may move from one configured exact alias to its
unique canonical name. The validator calls that resolver with the checkpoint's
saved `repository_hints`; it has no duplicate case, URL, basename, suffix, or
fuzzy logic. Unknown or ambiguous names and every non-repository argument drift
are rejected. Argument identity is recursive JSON type-and-value identity, not
ordinary Python equality: `true` is distinct from `1`, and integer `3` is
distinct from floating-point `3.0`. The same helper validates resumable and
completed traces, both before and after the permitted repository rewrite.

The latest saved fact is recomputed from the last decision and successful Tool
result. It must be the first fact in `ContextState`, and rebuilding with that
one protected fact must reproduce the saved next `ModelInput` exactly. This
includes the zero-evidence-budget case: the just-produced fact remains visible
to the resumed model rather than being reconstructed with ordinary trimming
and silently lost.

JSON loading is fail closed. Unknown schema versions, statuses, phases, fields,
or event types; duplicate object keys; non-finite numbers; primitive type
coercions; non-canonical trace payloads; corrupt JSON; task/sequence/budget
mismatches; incomplete model or Tool pairs; error Tool results; and finalized
`resumable` records are rejected. `FileCheckpointStore` revalidates even typed
callers, writes mode-`0600` same-directory temporary files, fsyncs file content,
atomically replaces the target, and fsyncs the containing directory. It only
accepts generation 1 as the first record and thereafter requires the same task
and exactly one generation of forward progress. A corrupt, stale, unrelated,
or completed existing file is never overwritten. A new run checks for an
existing record before appending `Session` or calling the model or a Tool.

Recovery is an explicit two-stage operation so every injected boundary shares
one recorder. The new process loads the record, calls
`ResumableCheckpoint.restore_trace()`, constructs a new traced model, router,
Tool executor, and `AgentLoop` around that restored recorder, then calls
`AgentLoop.resume(record)`. Before appending a `Step` or invoking the model or a
Tool, `resume` re-reads the current store, rejects completed or stale records,
and requires value-equivalent record state and an exact restored trace. It then
continues from the saved `next_model_input`, `next_step_number`, budget, Tool
count, and generation. It never appends a second `Session` and never replays the
Tool pair already present in the prefix.

Once a loop that has written at least one resumable generation reaches any
terminal, the store atomically advances to a `completed` record containing the
final state and finalized trace. Completed records require exactly one terminal
last event, correlated recorded model and Tool pairs, continuous budgets, and
at least one prior successful Tool result. A successful final model decision
must exactly match the terminal answer, normalized evidence, uncertainties, and
next queries, with `termination_reason="completed"`. A failure terminal must use
the stable answer and reason derivable from the last model error, rejected
evidence, exhausted budget, Tool error, timeout, or recorded Harness invariant.
These terminal rules share the same stable failure-answer constructor used by
`AgentLoop`; the checkpoint validator does not copy private error strings.
Completed records cannot be advanced or resumed. A task that terminates before
its first successful Tool result never creates a checkpoint file.

This contract proves no duplicate execution of the already completed Tool only
for this controlled recovery boundary. It does not recover a pending model
request, an executing Tool, or a failed Tool result. It does not provide worker
leasing, locking, concurrent-claim protection, encryption, schema migration,
or exactly-once semantics for external Tool side effects or arbitrary crash
points. Checkpoints intentionally contain safe task evidence, but never model
credentials, Base URLs, proxy configuration, provider-native responses,
exception text, runtime file paths, or private diagnostic logs.

### Final-answer evidence gate

At the model boundary, `FinalAnswerDecision.evidence` is a tuple of strict
`FinalAnswerCitation` objects containing only `repo`, `path`, and `line`.
`repo` and `path` must be non-empty relative source components without
surrounding whitespace, backslashes, colons, newlines, or `.` / `..` / empty
segments. `line` must be a strict positive integer, so booleans and numeric
strings are rejected. Extra or missing fields, legacy citation strings, and
line ranges fail as `invalid_model_output`; the boundary does not repair or
coerce them. `OpenAIModel` supplies both the matching JSON example and a strict
Chat Completions `json_schema` response format while retaining the existing
strict retrieval-tool schemas.

`TracedModelClient` and `AgentLoop` normalize each valid citation object to the
existing canonical `repo/path:line` representation. `ModelResult`,
`FinalAnswer`, and evaluation reports therefore retain their public string
format. The runtime gate accepts a final decision only when its normalized list
is non-empty and every entry exactly matches a citation derived from this
task's trace. The allowed set comes only from adjacent, correlated
`ToolCall -> ToolResult(status="success")` pairs recorded under the current
`task_id` before the terminal event. Caller-supplied `ContextState.evidence`,
history, model-authored citations, failed Tool results, other tasks, and
incomplete event pairs do not authorize a citation.

`search_code` authorizes each exact `repo`, `path`, and positive `line` in its
normalized `matches`. `get_file_context` authorizes each positive line in the
normalized result's actual inclusive `start_line -> end_line` range for its
exact `repository/file_path`. The validator constructs the complete strings
from these fields and uses exact set membership. It does not parse a submitted
string to guess repository/path boundaries, and it has no case, basename, URL,
suffix, or substring fallback.

An empty evidence list, a structurally valid but forged location, an
out-of-range context line, or a mixed authorized/unauthorized list fails closed as
`termination_reason="insufficient_evidence"`. The terminal replaces the model's
unsupported answer with one stable information-insufficient message and clears
`evidence`, `uncertainties`, and `next_queries`. It remains the unique final and
last event; no model or Tool call follows it. This gate is deliberately a
narrow provenance check, not a general claim verifier.

### Frozen agent evaluation boundary

`evaluation/agent_cases.jsonl` is a separate Agent-level dataset rather than a
copy of the retrieval queries. Its ten natural-language tasks, expected result
types, reviewed source ranges, pinned Click revision, and file SHA-256 are
validated before any real model call. The real CLI accepts only the default
frozen file and has no partial-run, retry, or best-of switch. Cases execute
sequentially once in file order; results never feed back into the system
instruction, Tool descriptions, retrieval rules, or gold data.

Every case record retains the task and gold identifier, `task_id`, structured
terminal, submitted final decision, attempted Tool count and name sequence,
per-citation parse/provenance/gold checks, recomputed Trace invariants, observed
model/Tool/task latency, and one stable raw failure category. Citation metrics
include rejected citations from the model's final decision even though the
runtime correctly clears them from an `insufficient_evidence` terminal. Empty
denominators remain `null`; the evaluator does not manufacture a passing rate.

Aggregate success, termination, Tool, citation, Trace, failure, and latency
metrics are pure recomputations from case details. The report gate requires at
least ten executed cases, every Tool count at most six, complete traces, a
non-empty citation denominator, and citation validity at least 95%. Task
success has no prefilled threshold and is always reported as an observed
fraction. Environment preflight separately verifies the checkout, declared
index revision, exact `zoekt.name`, pinned positive/negative index probes, and
the existing 18-case retrieval data contract.

Reports store no credentials, endpoint URL, local repository root, headers,
raw exceptions, or Provider responses. The Zoekt server used here does not
expose a build or embedded source revision, so index consistency is described
only through the operator-declared revision and pinned content probes. This is
an explicit limitation, not inferred metadata.

### Frozen termination matrix

The terminal producer in every row is `AgentLoop`, which constructs the event
and delegates the one terminal append to `TraceRecorder.finalize`. A boundary
that detects a failure first still owns its correlated non-terminal failure
event. Failure terminals contain one stable explanation and empty `evidence`,
`uncertainties`, and `next_queries`; they never copy exception text, provider
responses, or transport details.

| Path | `termination_reason` | Events retained before the terminal | Calls allowed after trigger | Status |
| --- | --- | --- | --- | --- |
| Structured final answer with non-empty, fully traceable evidence | `completed` | Session; every Step; all correlated model and successful tool pairs | None | Implemented |
| Empty, forged, out-of-range, or mixed structured final evidence | `insufficient_evidence` | Events through the successful `ModelResult` containing the rejected final decision | None | Implemented |
| Tool budget exhausted | `tool_budget_exhausted` | Events through the model result that requested the disallowed call; no ToolCall or ToolResult for it | None | Implemented |
| Model execution failure | `model_execution_error` | Current Step, ModelRequest, and its error ModelResult, plus all earlier events | None; no retry | Implemented |
| Invalid model output, including a malformed citation object or legacy citation string | `invalid_model_output` | Current Step, ModelRequest, and its error ModelResult, plus all earlier events | None; no retry | Implemented |
| Tool failure | `tool_error` | Successful model pair followed by the correlated ToolCall and error ToolResult, plus all earlier events | None; no retry | Implemented |
| Model timeout | `model_timeout` | Current Step, ModelRequest, and its timeout-classified error ModelResult, plus all earlier events | None; no retry | Implemented |
| Tool timeout | `tool_timeout` | Successful model pair, ToolCall, and its timeout-classified error ToolResult, plus all earlier events | None; no retry | Implemented |
| Harness invariant failure | `harness_invariant_error` | Only valid events recorded before the invariant was detected; no fabricated pair | None | Implemented |

Every row ends in exactly one terminal event. Boundary failures may expose only
their stable classifications; raw model, tool, or timeout exception text must
not enter state, trace, or the returned outcome. The loop catches only explicit
boundary and invariant errors. Unexpected programming errors still propagate
rather than being relabeled as harness failures.

Before consuming a decision or tool outcome, the loop verifies the immediately
preceding correlated event pair, including identifier, status, and serialized
decision/result. A missing result, mismatched identifier, contract-external
decision, or invalid budget transition terminates as `harness_invariant_error`
without fabricating the missing event. This is a post-condition check; parsing,
argument validation, call construction, and execution remain boundary-owned.

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
| `tool_timeout` | The invoked Tool raises the explicit timeout signal before producing a result. |

The trace carries the stable classification rather than backend exception text.
This keeps traces deterministic and avoids exposing implementation details; the
adapter owner remains responsible for diagnostic logging at its integration
boundary.

## Result normalization and deliberate limits

The router converts a Pydantic result model with `model_dump(mode="json")` and
then validates the outcome as JSON before creating a successful `ToolResult`.
This allows domain models such as `CodeSearchResponse` to cross the trace
boundary without coupling the trace schema to those models.

The design intentionally does not retry tools, enforce a timeout by spawning a
background thread, measure Tool wall-clock time, persist traces, or provide a
plugin registry. A caller-owned transport may enforce its timeout and raise the
explicit timeout signal; the router synchronously classifies that completed
failure before the loop can finalize. Retrying can duplicate side effects and
would obscure the one-call/one-result trace invariant. A fixed two-tool
allowlist is preferable here because the agent workflow is deliberately narrow;
adding another capability requires an explicit contract, adapter, and tests.

## Single tool-step transition

`ToolStepExecutor` is the provider-independent boundary between one validated
`ToolCallDecision` and the next model input. It performs exactly this
transition:

```text
ContextState + ToolCallDecision
  -> ToolCall -> ToolRouter.execute -> ToolResult
  -> next ContextState -> next ModelInput
```

When `remaining_tool_calls` is zero, the executor does not call the router or
an adapter, appends no trace events, returns no `ToolResult`, reuses the input
state unchanged, and builds the next input with the unchanged zero budget.
Otherwise, one attempted call consumes exactly one unit whether it succeeds,
is rejected during repository resolution, or returns a classified execution
failure. The executor does not retry.

Every call that reaches the router appends `ToolCall` and then its correlated
`ToolResult`, for both success and classified failure. After successful alias
resolution, the `ToolCall` contains the canonical repository name used by the
adapter while the caller's `ToolCallDecision` retains the model's original
alias. If resolution rejects the repository, no executable call exists, so the
recorded `ToolCall` retains the unresolved arguments before the error result.

The result becomes one `fact` evidence item. Successful evidence contains only
the normalized JSON result and status; failed evidence contains only the stable
router error classification and status. Raw exception text is neither copied
to state nor supplied to the next model input. The caller-owned state and
decision are never mutated.

Fresh tool evidence is placed before older evidence and is the only item
protected while the immediately following `ModelInput` is built. A protected
item must already exist in the next `ContextState`; at most one item can be
protected. It consumes the normal item budget first, but is retained even when
that budget is zero. This narrow override prevents the one completed tool call
from becoming invisible to the next model decision without providing a general
priority mechanism or an unbounded way around the evidence budget. Ordinary
standalone context builds keep the existing fact-first hard trimming behavior.

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
Provider/transport failures become `model_execution_error`; explicit provider
or built-in timeout exceptions become `model_timeout`; refusals, empty responses,
malformed JSON, multiple or unknown tool calls, and invalid arguments become
`invalid_model_output`. No error retains the provider exception or raw response.

`TracedModelClient` is provider-independent and can wrap both `FakeModel` and
`OpenAIModel`. It generates a local model `request_id`, appends a safe
`ModelRequest`, delegates once, then appends exactly one correlated
`ModelResult`. A successful result has a serialized decision and no error type;
an error result has only its stable error type. Every model result also has a
required non-negative `elapsed_ms`, measured with `time.perf_counter` around
the wrapped `decide` call, including failures and timeouts. This duration
includes SDK transport and response validation for `OpenAIModel`, but excludes
context construction and trace serialization. Tests inject a deterministic
clock instead of asserting real wall-clock timing.

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
The single tool-step transition may protect its one newly produced fact for the
immediately following model input, as described above.

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

## Structured incident extraction boundary

This boundary accepts only incident text that the caller has already reviewed
and redacted. It does not discover secrets, scrub arbitrary logs, read local
files, or send content to a model. Each `IncidentSource` is one caller-supplied
line with a stable, unique `source_id`, one explicit type (`description`, `log`,
or `stack_trace`), and non-empty text. Newline characters are rejected so a
source ID always identifies one concrete line; leading indentation and all
Unicode text are otherwise preserved exactly. Source IDs cannot contain
whitespace. There is no implicit default, trimming, truncation, count limit, or
length limit.

`IncidentInput.sources` is ordered. Python callers provide a tuple and JSON
callers provide the corresponding array. Duplicate IDs, unknown source types,
wrong scalar/container types, additional fields, empty input, and whitespace-
only text fail Pydantic's strict boundary rather than being repaired.

### Frozen structured output

An `IncidentExtractionCandidate` has exactly five required top-level fields:

| Field | Missing representation | Non-empty representation |
| --- | --- | --- |
| `exception_class` | `null` | one sourced string |
| `method` | `null` | one sourced string |
| `error_text` | `null` | one sourced string |
| `service_name` | `null` | one sourced string |
| `configuration_keys` | `[]` | an ordered array of sourced strings |

A sourced string contains only `value` and a non-empty ordered `source_ids`
array. Root cause, confidence, recommendations, missing-information guesses,
and arbitrary metadata are not part of this schema. Every top-level field must
be emitted even when it is missing, so adapters cannot replace absence with
`unknown`, `N/A`, or an omitted property. Configuration key order is retained;
duplicate key values and duplicate source references are rejected.

All strings are strict strings. In particular, JSON/Python `true`, integer `1`,
and float `1.0` cannot substitute for one another or for text. After provenance
validation, candidate and result payloads are also compared recursively by JSON
container type, key identity, array order, scalar runtime type, and scalar
value. This preserves the existing rule that ordinary Python equality must not
hide scalar type drift.

### Exact source-association gate

`validate_incident_extraction` resolves references only by the stable source
ID. For each non-null scalar and each configuration key, every cited ID must
exist and the value must occur as one contiguous, case-sensitive substring in
every cited source line. Case folding, normalization, fuzzy matching, array
position, and partial citation success are not accepted. An invalid candidate
raises `IncidentExtractionValidationError` and never becomes a trusted
`IncidentExtractionResult`.

`IncidentFieldExtractor` is an independent protocol. The offline
`FakeIncidentFieldExtractor` returns a caller-scripted candidate and records
detached input snapshots; it performs no extraction. `extract_incident_fields`
passes a deep copy to an extractor, checks the return type, and applies the
source gate against a separate pristine copy. Inputs, scripts, properties, and
trusted results are deep-copied at their boundaries, and stable JSON output
uses sorted object keys, fixed separators, and unescaped Unicode.

The extraction boundary itself stops before `ContextState`, tools, the Agent
Loop, checkpoints, a real model, or Zoekt. Passing the fake fixtures proves
only the input/output/provenance harness and deterministic serialization; it
is not an incident-extraction quality result and does not support causal
claims.

## Incident retrieval context mapping

`map_incident_to_retrieval_context` is a pure offline adapter between the
structured extraction boundary and the existing `ContextBuilder`. Its entry
point accepts both the original `IncidentInput` and an
`IncidentExtractionResult`. The result class is not treated as proof of trust:
the adapter deep-copies it, converts the copy back to an untrusted
`IncidentExtractionCandidate`, and reuses `validate_incident_extraction` before
constructing any retrieval task or context. Directly constructed or corrupted
results therefore fail on fabricated text, unknown IDs, case or Unicode drift,
and JSON scalar/container type drift at the same exact source-association gate.
The adapter neither copies nor weakens that validation rule.

### Ordered retrieval candidates

Every present observation becomes one `IncidentRetrievalTask` in this fixed
order:

1. `method`
2. `exception_class`
3. each `configuration_key`, preserving extraction order
4. `error_text`
5. `service_name`

Missing observations produce no placeholder. Equal values in different fields
are not merged, and the list is not truncated to `remaining_tool_calls`. Each
task retains its field name, original untrimmed query, and ordered source IDs,
plus a shared `SearchCodeArguments` object. The argument query is identical to
the observation, `literal=true`, and `lang` and `path` are `null`; the normal
router default owns the search limit. The task model rejects a mismatched
argument query, non-literal search, or guessed language/path.

`selected_repository` is a required explicit choice, including when the
choice is `null`. A non-null value must exactly match a
`RepositoryHint.canonical_name`. The mapper does not resolve aliases or infer a
repository from service names, package names, URLs, basenames, capitalization,
or approximate matches. Repository aliases remain available in context for
the existing execution-boundary resolver, but they are not accepted as a
mapper selection.

### Source facts and stable current task

The caller explicitly supplies `system_instruction`, `repository_hints`,
`selected_repository`, `evidence_item_budget`, and `remaining_tool_calls`; the
adapter has no production defaults for these policies. Each original incident
source becomes one `EvidenceKind.FACT` in input order. Its stable source label
is `incident:<source_type>:<source_id>`, and its content is the original
caller-redacted text without trimming or rewriting.

`ContextState.current_task` is canonical UTF-8-safe JSON containing a task type
and every ordered task payload. It retains the field name, query, source IDs,
and complete typed search arguments, so no natural-language parsing is needed
to reproduce the candidate list. Inputs, extraction results, repository hints,
tasks, state, and detached serialization payloads do not share caller-owned
mutable objects.

The state always contains all source facts. Evidence selection remains solely
the existing `ContextBuilder` policy: if `evidence_item_budget` is smaller than
the source count, the resulting `ModelInput` may contain fewer facts and the
adapter makes no claim that all sources survived construction. Mapping and
`ContextBuilder.build` do not invoke `ModelClient`, `ToolRouter`, Zoekt, MCP, or
the Agent Loop. These candidates are an inspectable plan only; they are not
executed searches, quality evidence, a root-cause ranking, or an Incident
Copilot result.

## Frozen Incident search replay boundary

`evaluation/replay_incident_search.py` is a narrow evaluation runner over the
mapper and the existing single Tool-step transition. It accepts only the
public `incident-click-unexpected-extra-argument-001` fixture. Before building
a decision, it verifies the fixture bytes against both the checked-in SHA-256
sidecar and the compiled frozen digest, parses a strict schema, repeats
`validate_incident_extraction`, and calls
`map_incident_to_retrieval_context`. It then requires exactly one
`error_text` task whose query, source ID, canonical `repo=click`, null
language/path, literal mode, default limit, and stable call ID match the frozen
contract. Source, repository, revision, gold, argument, or checksum drift
fails before a Tool can run.

The runner constructs a `ToolCallDecision` directly from that typed task; the
decision is not attributed to a model. It injects `search_code` and a
fail-if-called `get_file_context` handler into the existing
`PydanticToolAdapter -> ToolRouter -> ToolStepExecutor` path. Input budget is
one. Success, a classified Tool error, timeout, non-JSON return, malformed
search response, or missing gold all stop after the first attempt. There is no
retry, alternate query, result-driven fixture update, second Tool, model, or
Agent Loop.

Local preflight is separate and cannot invoke Zoekt. It requires explicit
Zoekt URL, repository root, and operator-declared index revision configuration;
then verifies Click checkout revision, exact `git config zoekt.name`, and the
frozen gold line at the pinned revision. The only network operation in a real
run is the one `src.server.search_code` execution after those checks pass.
This proves the direct Tool backend path, not MCP Client transport.

The allowlisted artifact records fixture/revision identity, repository,
content hashes, stable call counts, budget transition, Trace event types,
stable Tool status, gold rank and relative location, and Boolean state checks.
It excludes raw Incident/query text, source snippets, local paths, endpoint
values, credentials, and backend exception detail. A success requires the
frozen `repo/path/line/snippet` candidate and preservation of both the new Tool
fact and original Incident source fact in the next state and immediately next
`ModelInput`. A candidate match is retrieval evidence only; it is not a root
cause, confidence score, or troubleshooting recommendation.

## Frozen Incident context-suffix replay boundary

`evaluation/replay_incident_context.py` is a separate follow-up runner that
starts from the immutable successful search artifact rather than performing a
new search. It verifies the original Incident fixture and the complete
2026-08-30 search artifact by byte SHA-256, parses both with strict schemas,
and rechecks the successful preflight, one search call, exhausted search
budget, two-event Trace, state provenance, Tool-result digest, and exact
Top-1 `repo/path/line/snippet` identity. Missing, extra, reordered, or
type-drifted persisted fields fail before a Tool call; in particular, JSON
booleans and floats cannot substitute for the integer rank or line.

Only after those gates pass does the runner copy the Top-1 repository, path,
and line into a `GetFileContextArguments` object. The context window is frozen
at 20 lines before and after the target, and the runner creates a stable
caller-owned `ToolCallDecision`; it does not attribute that choice to a model.
The state contains both the original Incident source and a compact fact linked
to the upstream artifact and search-result hashes. Its new one-call budget is
independent of the already exhausted search budget.

The decision uses the existing
`PydanticToolAdapter -> ToolRouter -> ToolStepExecutor` transition. A
fail-if-called search adapter makes another search impossible, while the
context adapter counts exactly one attempted `get_file_context`. Success,
Tool error, timeout, invalid JSON, or source mismatch all stop without retry.
The two-event suffix Trace, `1 -> 0` budget, fresh context fact, validated
search fact, and original Incident fact are checked in both next state and the
immediately following `ModelInput`.

Zero-Tool preflight independently verifies the pinned Click checkout,
canonical `zoekt.name`, target file and line, the fixed `1264..1304` bounds,
and the expected context-content hash. A public artifact contains only stable
counts, relative source coordinates, hashes, statuses, and Boolean checks;
raw context, local roots, credentials, and backend exception details are
excluded. This proves a deterministic suffix across two dated artifacts. It
does not prove a same-Trace two-Tool plan, MCP transport, model selection,
root-cause quality, retry behavior, or a complete Incident workflow.

## Offline Incident analysis contract (2026-09-05)

An independent `incident_analysis` boundary accepts an untrusted Candidate and
returns a detached frozen Result. It does not extend FinalAnswerDecision.
All four ordered arrays are required: facts, hypotheses, missing_information,
and investigation_steps. IDs are nonempty, whitespace-free and unique within
each category; links are local to the explicitly pinned case_id.

Facts contain only an exact Incident excerpt with source_ids, or the verified
single-line code citation and fixed snippet. There is no free-text explanation
field in a fact. Incident excerpts reuse the extraction provenance validator;
code citations reuse FinalAnswerCitation and must equal the validated fixture's
repo/path/line/snippet. The entry point reloads the fixture and search artifact
through load_context_replay and validates context bytes through the public
validate_execution_artifact; a successful context is required. No new artifact
hash validator or relaxed dictionary comparison is introduced.

Hypotheses require a statement, at least one unique existing fact ID, and
low/medium/high confidence. Links establish provenance, not causal entailment;
confidence is neither calibrated nor assessed by this validator. Empty
hypotheses are valid only with at least one missing-information question.
Questions and ordered investigation actions must be nonblank. Step hypothesis
links are optional (an explicitly empty array), unique and must exist. Steps
are plans, never executed here. Their wording, usefulness and operational
safety are not semantically certified. No step-result field is accepted.

Strict required fields reject extras, coercion, scalar type drift, old flat
answers and dangling references. Python inputs are strictly re-parsed before
use, even if constructed as models; JSON has strict scalar parsing and ordered
arrays. All boundary inputs and outputs are detached. The frozen model and
tuple tree prohibit ordinary mutation; exported payloads are fresh copies.
The current Click case alone is supported. No model, Tool, loop, transport or
network execution occurs. This contract does not measure extraction quality,
root-cause accuracy or production diagnostic ability.
