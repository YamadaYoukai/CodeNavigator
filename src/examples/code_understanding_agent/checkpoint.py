"""Strict, deterministic persistence for safe agent-loop recovery points."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from .context import (
    ContextBuilder,
    ContextState,
    Evidence,
    EvidenceKind,
    ModelInput,
    RepositoryHint,
)
from .events import (
    FinalAnswer,
    ModelRequest,
    ModelResult,
    RecordedEvent,
    Session,
    Step,
    ToolCall,
    ToolResult,
)
from .final_answer_evidence import validate_final_answer_evidence
from .limits import MAX_TOOL_CALLS_PER_TASK
from .model_boundary import ToolCallDecision
from .repository_resolver import RepositoryAliasResolver
from .termination import (
    FAILURE_TERMINATION_REASONS,
    FailureTerminationReason,
    build_failure_final_answer,
)
from .tool_router import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    ToolCallResolutionError,
    ToolErrorCode,
)
from .trace import TraceRecorder


CHECKPOINT_SCHEMA_VERSION = 1
RESUMABLE_PHASE = "after_successful_tool_result"
COMPLETED_PHASE = "completed"

StrictPositiveInt = Annotated[int, Field(strict=True, ge=1)]
StrictGenerationAtLeastTwo = Annotated[int, Field(strict=True, ge=2)]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictNonEmptyText = Annotated[str, Field(strict=True, min_length=1)]


class _RecordedFinalAnswerDecision(BaseModel):
    """Canonical trace representation of a validated final model decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    decision_type: Literal["final_answer"]
    answer: StrictNonEmptyText
    evidence: tuple[StrictNonEmptyText, ...]
    uncertainties: tuple[StrictNonEmptyText, ...]
    next_queries: tuple[StrictNonEmptyText, ...]


class CheckpointError(ValueError):
    """Base class for stable, fail-closed checkpoint errors."""


class CheckpointContractError(CheckpointError):
    """The serialized checkpoint does not satisfy the frozen contract."""


class CheckpointStoreError(CheckpointError):
    """The checkpoint file could not be read or atomically advanced."""


class CheckpointResumeError(CheckpointError):
    """A checkpoint cannot safely be used to resume this loop."""


class _CheckpointRecordBase(BaseModel):
    """Fields shared by resumable checkpoints and completed records."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = CHECKPOINT_SCHEMA_VERSION
    status: str
    task_id: str = Field(min_length=1)
    generation: StrictPositiveInt
    phase: str
    trace: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """Return a detached, JSON-compatible record."""

        return self.model_dump(mode="json")

    def to_json(self) -> str:
        """Serialize with stable key ordering and no timestamp or host path."""

        return json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def restore_trace(self) -> TraceRecorder:
        """Build a new recorder from the validated detached trace snapshot."""

        return TraceRecorder.from_dict(self.trace)


class ResumableCheckpoint(_CheckpointRecordBase):
    """The only state from which an ``AgentLoop`` may continue execution."""

    status: Literal["resumable"] = "resumable"
    phase: Literal["after_successful_tool_result"] = RESUMABLE_PHASE
    state: ContextState
    next_model_input: ModelInput
    tool_calls_used: StrictPositiveInt
    remaining_tool_calls: StrictNonNegativeInt
    next_step_number: StrictPositiveInt

    @model_validator(mode="after")
    def validate_recovery_boundary(self) -> "ResumableCheckpoint":
        trace = _restore_canonical_trace(
            self.trace,
            task_id=self.task_id,
            expected_finalized=False,
        )
        _validate_resumable_trace(self, trace.events)
        return self


class CompletedCheckpoint(_CheckpointRecordBase):
    """A terminal persistence record which can never be resumed."""

    status: Literal["completed"] = "completed"
    generation: StrictGenerationAtLeastTwo
    phase: Literal["completed"] = COMPLETED_PHASE
    final_state: ContextState
    tool_calls_used: StrictNonNegativeInt
    remaining_tool_calls: StrictNonNegativeInt

    @model_validator(mode="after")
    def validate_terminal_record(self) -> "CompletedCheckpoint":
        trace = _restore_canonical_trace(
            self.trace,
            task_id=self.task_id,
            expected_finalized=True,
        )
        _validate_completed_trace(self, trace.events)
        return self


CheckpointRecord: TypeAlias = ResumableCheckpoint | CompletedCheckpoint
_CHECKPOINT_ADAPTER = TypeAdapter(
    Annotated[CheckpointRecord, Field(discriminator="status")]
)


class CheckpointStore(Protocol):
    """Persistence boundary required by ``AgentLoop``."""

    def has_record(self) -> bool:
        """Return whether a new run would collide with an existing record."""
        ...

    def load(self) -> CheckpointRecord:
        """Load and strictly validate the current record."""
        ...

    def save(self, record: CheckpointRecord) -> None:
        """Atomically advance the record by exactly one generation."""
        ...


def checkpoint_from_dict(payload: Mapping[str, Any]) -> CheckpointRecord:
    """Validate a decoded record without repairing or ignoring input."""

    if not isinstance(payload, Mapping):
        raise CheckpointContractError("checkpoint must encode an object")
    try:
        # Strict JSON mode accepts JSON arrays for tuple fields while still
        # rejecting primitive coercions such as ``"1"`` to integer ``1``.
        canonical_json = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        return _CHECKPOINT_ADAPTER.validate_json(canonical_json, strict=True)
    except (TypeError, ValueError, ValidationError):
        raise CheckpointContractError(
            "checkpoint contract validation failed"
        ) from None


def checkpoint_from_json(payload: str) -> CheckpointRecord:
    """Decode strict JSON, rejecting duplicate keys and non-finite numbers."""

    if not isinstance(payload, str):
        raise CheckpointContractError("checkpoint JSON must be text")
    try:
        decoded = json.loads(
            payload,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_finite_number,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        raise CheckpointContractError("checkpoint JSON is invalid") from None
    if not isinstance(decoded, dict):
        raise CheckpointContractError("checkpoint must encode an object")
    return checkpoint_from_dict(decoded)


class FileCheckpointStore:
    """One-record file store using fsync and same-directory atomic replace."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        if not isinstance(path, (str, os.PathLike)):
            raise TypeError("path must be a string or path-like object")
        resolved = Path(path)
        if not resolved.name:
            raise ValueError("path must identify a checkpoint file")
        self._path = resolved

    @property
    def path(self) -> Path:
        """Return the configured runtime location; it is never serialized."""

        return self._path

    def has_record(self) -> bool:
        """Return whether the target currently names an existing record."""

        return self._path.exists()

    def load(self) -> CheckpointRecord:
        """Read the current file and fail closed on any I/O or contract error."""

        try:
            payload = self._path.read_text(encoding="utf-8")
        except OSError:
            raise CheckpointStoreError("checkpoint file is unavailable") from None
        try:
            return checkpoint_from_json(payload)
        except CheckpointContractError:
            raise CheckpointStoreError("checkpoint file is invalid") from None

    def save(self, record: CheckpointRecord) -> None:
        """Advance one generation without overwriting stale or terminal state."""

        if not isinstance(record, (ResumableCheckpoint, CompletedCheckpoint)):
            raise TypeError("record must be a checkpoint record")
        # ``model_copy(update=...)`` deliberately skips Pydantic validation.
        # Re-validate even typed callers so it cannot bypass the disk contract.
        record = checkpoint_from_dict(record.to_payload())

        current: CheckpointRecord | None = None
        if self._path.exists():
            current = self.load()

        if current is None:
            if not isinstance(record, ResumableCheckpoint) or record.generation != 1:
                raise CheckpointStoreError(
                    "the first checkpoint must be resumable generation 1"
                )
        else:
            if isinstance(current, CompletedCheckpoint):
                raise CheckpointStoreError("completed checkpoint cannot be advanced")
            if record.task_id != current.task_id:
                raise CheckpointStoreError("checkpoint task_id cannot change")
            if record.generation != current.generation + 1:
                raise CheckpointStoreError(
                    "checkpoint generation must advance by exactly one"
                )
            _validate_store_transition(current, record)

        self._atomic_write(record.to_json() + "\n")

    def _atomic_write(self, payload: str) -> None:
        parent = self._path.parent
        descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            descriptor, raw_temporary_path = tempfile.mkstemp(
                dir=parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(raw_temporary_path)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                descriptor = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
            temporary_path = None
            _fsync_directory(parent)
        except OSError:
            raise CheckpointStoreError(
                "checkpoint atomic write failed"
            ) from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass


def _restore_canonical_trace(
    payload: Mapping[str, Any],
    *,
    task_id: str,
    expected_finalized: bool,
) -> TraceRecorder:
    if set(payload) != {"task_id", "events", "finalized"}:
        raise ValueError("checkpoint trace envelope is not exact")
    trace = TraceRecorder.from_dict(payload)
    if trace.task_id != task_id:
        raise ValueError("checkpoint task_id does not match trace")
    if trace.is_finalized is not expected_finalized:
        raise ValueError("checkpoint trace terminal state is invalid")
    if trace.to_dict() != payload:
        raise ValueError("checkpoint trace is not canonical")
    return trace


def _json_values_are_identical(left: object, right: object) -> bool:
    """Compare JSON values without Python's bool/number equivalence."""

    if isinstance(left, Mapping):
        if not isinstance(right, Mapping) or set(left) != set(right):
            return False
        return all(
            _json_values_are_identical(left[key], right[key]) for key in left
        )
    if isinstance(right, Mapping):
        return False
    if isinstance(left, list):
        if not isinstance(right, list) or len(left) != len(right):
            return False
        return all(
            _json_values_are_identical(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(right, list):
        return False
    return type(left) is type(right) and left == right


def _validate_tool_decision_call(
    decision: ToolCallDecision,
    call: ToolCall,
    repository_hints: Sequence[RepositoryHint],
) -> None:
    """Require an exact call or the resolver's one allowed repository rewrite."""

    if call.call_id != decision.call_id or call.tool_name != decision.tool_name:
        raise ValueError("trace tool call identity does not match model decision")
    if _json_values_are_identical(call.arguments, decision.arguments):
        return

    model_call = ToolCall(
        call_id=decision.call_id,
        tool_name=decision.tool_name,
        arguments=decision.arguments,
    )
    try:
        resolved_call = RepositoryAliasResolver(repository_hints).resolve(model_call)
    except (ToolCallResolutionError, TypeError, ValueError):
        raise ValueError(
            "trace tool arguments do not match model decision"
        ) from None
    if not _json_values_are_identical(
        call.arguments,
        resolved_call.arguments,
    ):
        raise ValueError("trace tool arguments do not match model decision")


def _validated_final_answer_decision(
    payload: Mapping[str, Any],
) -> _RecordedFinalAnswerDecision:
    try:
        canonical_json = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        decision = _RecordedFinalAnswerDecision.model_validate_json(
            canonical_json,
            strict=True,
        )
    except (TypeError, ValueError, ValidationError):
        raise ValueError("completed final model decision is invalid") from None
    if decision.model_dump(mode="json") != payload:
        raise ValueError("completed final model decision is not canonical")
    return decision


def _validate_success_final_answer(
    final_answer: FinalAnswer,
    decision: _RecordedFinalAnswerDecision,
) -> None:
    if (
        final_answer.answer != decision.answer
        or tuple(final_answer.evidence) != decision.evidence
        or tuple(final_answer.uncertainties) != decision.uncertainties
        or tuple(final_answer.next_queries) != decision.next_queries
        or final_answer.termination_reason != "completed"
    ):
        raise ValueError("completed FinalAnswer does not match model decision")


def _validate_failure_final_answer(
    final_answer: FinalAnswer,
    reason: FailureTerminationReason,
) -> None:
    expected = build_failure_final_answer(reason)
    if (
        final_answer.answer != expected.answer
        or final_answer.evidence != expected.evidence
        or final_answer.uncertainties != expected.uncertainties
        or final_answer.next_queries != expected.next_queries
        or final_answer.termination_reason != expected.termination_reason
    ):
        raise ValueError("completed failure FinalAnswer is inconsistent")


def _validate_resumable_trace(
    checkpoint: ResumableCheckpoint,
    events: Sequence[RecordedEvent],
) -> None:
    if not events or not isinstance(events[0], Session):
        raise ValueError("resumable trace must start with Session")
    session = events[0]
    if session.user_task != checkpoint.state.current_task:
        raise ValueError("session task does not match checkpoint state")
    if session.available_tools != [SEARCH_CODE, GET_FILE_CONTEXT]:
        raise ValueError("session tool allowlist is invalid")

    index = 1
    expected_step = 1
    initial_budget: int | None = None
    latest_decision: ToolCallDecision | None = None
    latest_result: ToolResult | None = None
    request_inputs: list[ModelInput] = []
    tool_facts: list[Evidence] = []

    while index < len(events):
        if index + 4 >= len(events):
            raise ValueError("resumable trace contains an incomplete event group")
        step, request, result, call, tool_result = events[index : index + 5]
        if not isinstance(step, Step) or step.step_number != expected_step:
            raise ValueError("resumable trace step numbers are invalid")
        if not isinstance(request, ModelRequest) or not isinstance(result, ModelResult):
            raise ValueError("resumable trace model pair is invalid")
        if (
            request.request_id != result.request_id
            or result.status != "success"
            or result.decision is None
        ):
            raise ValueError("resumable trace model pair is not correlated")
        try:
            decision = ToolCallDecision.model_validate(result.decision, strict=True)
        except (TypeError, ValueError, ValidationError):
            raise ValueError("resumable model result is not a tool decision") from None
        if not isinstance(call, ToolCall) or not isinstance(tool_result, ToolResult):
            raise ValueError("resumable trace tool pair is invalid")
        _validate_tool_decision_call(
            decision,
            call,
            checkpoint.state.repository_hints,
        )
        if (
            tool_result.call_id != call.call_id
            or tool_result.status != "success"
        ):
            raise ValueError("resumable trace tool pair is not correlated")

        model_input = _validated_request_input(request)
        _validate_static_input(model_input, checkpoint.state)
        if initial_budget is None:
            initial_budget = model_input.remaining_tool_calls
            if initial_budget > MAX_TOOL_CALLS_PER_TASK:
                raise ValueError("initial tool budget exceeds the hard limit")
        expected_budget = initial_budget - (expected_step - 1)
        if model_input.remaining_tool_calls != expected_budget:
            raise ValueError("model request budgets are not continuous")

        request_inputs.append(model_input)
        tool_facts.append(_tool_result_fact(decision, tool_result))
        latest_decision = decision
        latest_result = tool_result
        expected_step += 1
        index += 5

    tool_pair_count = expected_step - 1
    if tool_pair_count == 0 or initial_budget is None:
        raise ValueError("resumable trace must contain a successful tool pair")
    if checkpoint.tool_calls_used != tool_pair_count:
        raise ValueError("checkpoint tool count does not match trace")
    if checkpoint.next_step_number != expected_step:
        raise ValueError("checkpoint next step does not match trace")
    expected_remaining = initial_budget - tool_pair_count
    if expected_remaining < 0:
        raise ValueError("checkpoint tool budget is negative")
    if (
        checkpoint.remaining_tool_calls != expected_remaining
        or checkpoint.state.remaining_tool_calls != expected_remaining
        or checkpoint.next_model_input.remaining_tool_calls != expected_remaining
    ):
        raise ValueError("checkpoint tool budgets do not agree")

    assert latest_decision is not None and latest_result is not None
    expected_fact_prefix = tuple(reversed(tool_facts))
    if checkpoint.state.evidence[:tool_pair_count] != expected_fact_prefix:
        raise ValueError("checkpoint state tool-fact history does not match trace")
    initial_evidence = checkpoint.state.evidence[tool_pair_count:]
    for prior_tool_count, observed_input in enumerate(request_inputs):
        prior_facts = tuple(reversed(tool_facts[:prior_tool_count]))
        request_state = checkpoint.state.model_copy(
            update={
                "evidence": (*prior_facts, *initial_evidence),
                "remaining_tool_calls": initial_budget - prior_tool_count,
            },
            deep=True,
        )
        protected = prior_facts[:1]
        expected_request_input = ContextBuilder().build(
            request_state,
            protected_evidence=protected,
        )
        if observed_input != expected_request_input:
            raise ValueError("checkpoint model-input history does not match trace")

    latest_fact = tool_facts[-1]
    expected_input = ContextBuilder().build(
        checkpoint.state,
        protected_evidence=(latest_fact,),
    )
    if checkpoint.next_model_input != expected_input:
        raise ValueError("checkpoint does not preserve the exact next ModelInput")


def _validate_store_transition(
    current: ResumableCheckpoint,
    candidate: CheckpointRecord,
) -> None:
    """Require the next generation to extend and consume the current input."""

    current_events = current.trace["events"]
    candidate_events = candidate.trace["events"]
    if (
        len(candidate_events) <= len(current_events)
        or candidate_events[: len(current_events)] != current_events
    ):
        raise CheckpointStoreError(
            "checkpoint trace must strictly extend the current prefix"
        )

    appended = candidate_events[len(current_events) :]
    if len(appended) < 3:
        raise CheckpointStoreError(
            "checkpoint trace extension is incomplete"
        )
    if isinstance(candidate, ResumableCheckpoint) and len(appended) != 5:
        raise CheckpointStoreError(
            "one resumable generation must contain exactly one Tool round"
        )
    step_payload, request_payload = appended[:2]
    if (
        not isinstance(step_payload, Mapping)
        or step_payload.get("event_type") != "step"
        or step_payload.get("step_number") != current.next_step_number
        or not isinstance(request_payload, Mapping)
        or request_payload.get("event_type") != "model_request"
        or request_payload.get("model_input")
        != current.next_model_input.to_payload()
    ):
        raise CheckpointStoreError(
            "checkpoint trace did not consume the saved next ModelInput"
        )


def _validate_completed_trace(
    checkpoint: CompletedCheckpoint,
    events: Sequence[RecordedEvent],
) -> None:
    if not events or not isinstance(events[0], Session):
        raise ValueError("completed trace must start with Session")
    if not isinstance(events[-1], FinalAnswer):
        raise ValueError("completed trace must end with FinalAnswer")
    final_answer = events[-1]
    if sum(isinstance(event, Session) for event in events) != 1:
        raise ValueError("completed trace must contain one Session")
    if sum(isinstance(event, FinalAnswer) for event in events) != 1:
        raise ValueError("completed trace must contain one FinalAnswer")
    if events[0].user_task != checkpoint.final_state.current_task:
        raise ValueError("completed session task does not match final state")
    if events[0].available_tools != [SEARCH_CODE, GET_FILE_CONTEXT]:
        raise ValueError("completed session tool allowlist is invalid")

    expected_step = 1
    model_requests: list[tuple[int, ModelInput]] = []
    tool_results: list[tuple[int, ToolResult]] = []
    ended_with_unrecorded_tool_attempt = False
    terminal_failure_reason: FailureTerminationReason | None = None
    terminal_success_decision: _RecordedFinalAnswerDecision | None = None
    terminal_index = len(events) - 1
    index = 1
    while index < terminal_index:
        if index + 2 >= terminal_index:
            raise ValueError("completed trace contains an incomplete model group")
        step, request, model_result = events[index : index + 3]
        if not isinstance(step, Step) or step.step_number != expected_step:
            raise ValueError("completed trace step numbers are invalid")
        if not isinstance(request, ModelRequest) or not isinstance(
            model_result, ModelResult
        ):
            raise ValueError("completed trace model group is invalid")
        if model_result.request_id != request.request_id:
            raise ValueError("completed trace model pair is not correlated")

        model_input = _validated_request_input(request)
        _validate_static_input(model_input, checkpoint.final_state)
        model_requests.append((index + 1, model_input))
        expected_step += 1
        index += 3

        if model_result.status == "error":
            if index != terminal_index:
                raise ValueError("events follow a failed model result")
            if model_result.error_type not in FAILURE_TERMINATION_REASONS:
                raise ValueError("completed model failure type is invalid")
            terminal_failure_reason = model_result.error_type
            break

        decision = model_result.decision
        if not isinstance(decision, Mapping):
            raise ValueError("completed model decision is invalid")
        decision_type = decision.get("decision_type")
        if decision_type == "final_answer":
            if index != terminal_index:
                raise ValueError("events follow a final model decision")
            final_decision = _validated_final_answer_decision(decision)
            evidence_validation = validate_final_answer_evidence(
                final_decision.evidence,
                events=events,
                task_id=checkpoint.task_id,
            )
            if evidence_validation.is_valid:
                terminal_success_decision = final_decision
            else:
                terminal_failure_reason = "insufficient_evidence"
            break
        if decision_type != "tool_call":
            raise ValueError("completed model decision type is invalid")
        try:
            tool_decision = ToolCallDecision.model_validate(decision, strict=True)
        except (TypeError, ValueError, ValidationError):
            raise ValueError("completed tool decision is invalid") from None

        # A Tool decision can terminate directly when its budget is exhausted
        # or a harness post-condition failed before a ToolCall was recorded.
        if index == terminal_index:
            ended_with_unrecorded_tool_attempt = True
            if model_input.remaining_tool_calls == 0:
                terminal_failure_reason = "tool_budget_exhausted"
            else:
                terminal_failure_reason = "harness_invariant_error"
            break
        if index + 1 >= terminal_index:
            raise ValueError("completed trace contains a dangling tool call")
        call, tool_result = events[index : index + 2]
        if not isinstance(call, ToolCall) or not isinstance(tool_result, ToolResult):
            raise ValueError("completed trace tool pair is invalid")
        _validate_tool_decision_call(
            tool_decision,
            call,
            checkpoint.final_state.repository_hints,
        )
        if tool_result.call_id != call.call_id:
            raise ValueError("completed trace tool pair is not correlated")
        tool_results.append((index + 1, tool_result))
        index += 2
        if tool_result.status == "error" and index != terminal_index:
            raise ValueError("events follow a failed tool result")
        if tool_result.status == "error":
            if tool_result.error_type not in {
                error_code.value for error_code in ToolErrorCode
            }:
                raise ValueError("completed Tool failure type is invalid")
            if tool_result.error_type == ToolErrorCode.TOOL_TIMEOUT.value:
                terminal_failure_reason = "tool_timeout"
            else:
                terminal_failure_reason = "tool_error"
        elif index == terminal_index:
            raise ValueError("successful Tool result has no following model step")

    if index != terminal_index:
        raise ValueError("completed trace does not reach its terminal exactly")

    if not model_requests:
        raise ValueError("completed checkpoint must contain a model request")
    successful_results = [
        result for _, result in tool_results if result.status == "success"
    ]
    if not successful_results:
        raise ValueError("completed checkpoint must follow a resumable generation")
    recorded_tool_count = len(tool_results)
    expected_tool_count = recorded_tool_count
    if (
        ended_with_unrecorded_tool_attempt
        and terminal_failure_reason == "harness_invariant_error"
    ):
        expected_tool_count += 1
    if checkpoint.tool_calls_used != expected_tool_count:
        raise ValueError("completed tool count does not match trace")
    if checkpoint.tool_calls_used > MAX_TOOL_CALLS_PER_TASK:
        raise ValueError("completed tool count exceeds the hard limit")
    initial_budget = model_requests[0][1].remaining_tool_calls
    if initial_budget > MAX_TOOL_CALLS_PER_TASK:
        raise ValueError("completed initial budget exceeds the hard limit")
    for request_index, model_input in model_requests:
        consumed_before = sum(
            result_index < request_index for result_index, _ in tool_results
        )
        if model_input.remaining_tool_calls != initial_budget - consumed_before:
            raise ValueError("completed model request budgets are not continuous")
    expected_remaining = initial_budget - recorded_tool_count
    if (
        expected_remaining < 0
        or checkpoint.remaining_tool_calls != expected_remaining
        or checkpoint.final_state.remaining_tool_calls != expected_remaining
    ):
        raise ValueError("completed checkpoint budgets do not agree")

    if terminal_success_decision is not None:
        if terminal_failure_reason is not None:
            raise ValueError("completed trace has conflicting terminal semantics")
        _validate_success_final_answer(final_answer, terminal_success_decision)
    elif terminal_failure_reason is not None:
        _validate_failure_final_answer(final_answer, terminal_failure_reason)
    else:
        raise ValueError("completed trace does not explain its FinalAnswer")


def _validated_request_input(request: ModelRequest) -> ModelInput:
    try:
        model_input = ModelInput.model_validate_json(
            json.dumps(
                request.model_input,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ),
            strict=True,
        )
    except (TypeError, ValueError, ValidationError):
        raise ValueError("trace contains an invalid ModelInput") from None
    if model_input.to_payload() != request.model_input:
        raise ValueError("trace ModelInput is not canonical")
    return model_input


def _validate_static_input(model_input: ModelInput, state: ContextState) -> None:
    canonical_schemas = ContextBuilder().build(state).tool_schemas
    if (
        model_input.system_instruction != state.system_instruction
        or model_input.current_task != state.current_task
        or model_input.repository_hints != state.repository_hints
        or model_input.tool_schemas != canonical_schemas
    ):
        raise ValueError("trace ModelInput does not match checkpoint state")


def _tool_result_fact(
    decision: ToolCallDecision,
    result: ToolResult,
) -> Evidence:
    content = {"result": result.result, "status": "success"}
    return Evidence(
        kind=EvidenceKind.FACT,
        source=f"tool_result:{decision.tool_name}:{decision.call_id}",
        content=json.dumps(
            content,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in pairs:
        if key in decoded:
            raise ValueError("duplicate JSON object key")
        decoded[key] = value
    return decoded


def _reject_non_finite_number(value: str) -> None:
    raise ValueError(f"non-finite number is not valid JSON: {value}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
