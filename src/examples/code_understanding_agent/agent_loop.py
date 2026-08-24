"""Deterministic termination orchestration for the code-understanding agent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .checkpoint import (
    CheckpointContractError,
    CheckpointRecord,
    CheckpointResumeError,
    CheckpointStore,
    CompletedCheckpoint,
    ResumableCheckpoint,
    checkpoint_from_dict,
)
from .context import ContextBuilder, ContextState, ModelInput
from .events import (
    FinalAnswer,
    ModelRequest,
    ModelResult,
    Session,
    Step,
    ToolCall,
    ToolResult,
)
from .final_answer_evidence import validate_final_answer_evidence
from .limits import MAX_TOOL_CALLS_PER_TASK
from .model_boundary import (
    FinalAnswerDecision,
    ModelClient,
    ToolCallDecision,
    model_decision_to_trace_payload,
)
from .model_errors import ModelBoundaryError, ModelErrorType
from .termination import FailureTerminationReason, build_failure_final_answer
from .tool_router import GET_FILE_CONTEXT, SEARCH_CODE, ToolErrorCode
from .tool_step import (
    ToolStepExecutor,
    ToolStepInvariantError,
    ToolStepOutcome,
)
from .trace import TraceRecorder


@dataclass(frozen=True, slots=True)
class AgentLoopOutcome:
    """Detached terminal outputs of one agent loop."""

    final_answer: FinalAnswer
    final_state: ContextState
    tool_calls_used: int


class AgentLoop:
    """Orchestrate model and Tool boundaries into exactly one terminal event."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        model: ModelClient,
        tool_step_executor: ToolStepExecutor,
        trace: TraceRecorder,
        checkpoint_store: CheckpointStore | None = None,
        on_checkpoint_saved: Callable[[ResumableCheckpoint], None] | None = None,
    ) -> None:
        if not isinstance(context_builder, ContextBuilder):
            raise TypeError("context_builder must be a ContextBuilder")
        if not callable(getattr(model, "decide", None)):
            raise TypeError("model must provide decide(model_input)")
        if not isinstance(tool_step_executor, ToolStepExecutor):
            raise TypeError("tool_step_executor must be a ToolStepExecutor")
        if not isinstance(trace, TraceRecorder):
            raise TypeError("trace must be a TraceRecorder")
        if checkpoint_store is not None and (
            not callable(getattr(checkpoint_store, "has_record", None))
            or not callable(getattr(checkpoint_store, "load", None))
            or not callable(getattr(checkpoint_store, "save", None))
        ):
            raise TypeError(
                "checkpoint_store must provide has_record(), load(), and save()"
            )
        if on_checkpoint_saved is not None and not callable(on_checkpoint_saved):
            raise TypeError("on_checkpoint_saved must be callable")
        if on_checkpoint_saved is not None and checkpoint_store is None:
            raise ValueError("on_checkpoint_saved requires checkpoint_store")

        self._context_builder = context_builder
        self._model = model
        self._tool_step_executor = tool_step_executor
        self._trace = trace
        self._checkpoint_store = checkpoint_store
        self._on_checkpoint_saved = on_checkpoint_saved

    async def run(self, initial_state: ContextState) -> AgentLoopOutcome:
        """Run sequential decisions until one success or stable failure terminal."""

        if not isinstance(initial_state, ContextState):
            raise TypeError("initial_state must be a ContextState")
        if self._trace.events or self._trace.is_finalized:
            raise ValueError("trace must be empty and unfinalized")
        if (
            self._checkpoint_store is not None
            and self._checkpoint_store.has_record()
        ):
            # Validate the existing file as well as refusing to overwrite it.
            self._checkpoint_store.load()
            raise CheckpointResumeError(
                "checkpoint store already contains a record"
            )

        state = initial_state.model_copy(
            update={
                "remaining_tool_calls": min(
                    initial_state.remaining_tool_calls,
                    MAX_TOOL_CALLS_PER_TASK,
                )
            },
            deep=True,
        )
        model_input = self._context_builder.build(state)
        tool_calls_used = 0
        step_number = 1

        self._trace.append(
            Session(
                user_task=state.current_task,
                available_tools=[SEARCH_CODE, GET_FILE_CONTEXT],
            )
        )

        return await self._continue(
            state=state,
            model_input=model_input,
            tool_calls_used=tool_calls_used,
            step_number=step_number,
            checkpoint_generation=0,
        )

    async def resume(self, checkpoint: CheckpointRecord) -> AgentLoopOutcome:
        """Continue from one validated, current resumable checkpoint.

        The caller restores ``checkpoint.trace`` first and uses that new
        recorder for the loop, traced model, and Tool router. This method then
        re-reads the store and checks every precondition before the first Step,
        model call, or Tool call is permitted.
        """

        if isinstance(checkpoint, CompletedCheckpoint):
            raise CheckpointResumeError("completed checkpoint cannot be resumed")
        if not isinstance(checkpoint, ResumableCheckpoint):
            raise TypeError("checkpoint must be a checkpoint record")
        try:
            validated_checkpoint = checkpoint_from_dict(checkpoint.to_payload())
        except CheckpointContractError:
            raise CheckpointResumeError("checkpoint contract is invalid") from None
        if not isinstance(validated_checkpoint, ResumableCheckpoint):
            raise CheckpointResumeError("completed checkpoint cannot be resumed")
        checkpoint = validated_checkpoint
        if self._checkpoint_store is None:
            raise CheckpointResumeError(
                "checkpoint_store is required to resume safely"
            )

        stored_record = self._checkpoint_store.load()
        if isinstance(stored_record, CompletedCheckpoint):
            raise CheckpointResumeError("completed checkpoint cannot be resumed")
        if not isinstance(stored_record, ResumableCheckpoint):
            raise CheckpointResumeError("stored checkpoint contract is invalid")
        try:
            validated_stored_record = checkpoint_from_dict(
                stored_record.to_payload()
            )
        except CheckpointContractError:
            raise CheckpointResumeError(
                "stored checkpoint contract is invalid"
            ) from None
        if not isinstance(validated_stored_record, ResumableCheckpoint):
            raise CheckpointResumeError("completed checkpoint cannot be resumed")
        stored_record = validated_stored_record
        if stored_record != checkpoint:
            raise CheckpointResumeError("checkpoint is stale or was replaced")
        if self._trace.is_finalized or self._trace.to_dict() != checkpoint.trace:
            raise CheckpointResumeError(
                "loop trace does not match the resumable checkpoint"
            )

        return await self._continue(
            state=checkpoint.state.model_copy(deep=True),
            model_input=checkpoint.next_model_input.model_copy(deep=True),
            tool_calls_used=checkpoint.tool_calls_used,
            step_number=checkpoint.next_step_number,
            checkpoint_generation=checkpoint.generation,
        )

    async def _continue(
        self,
        *,
        state: ContextState,
        model_input: ModelInput,
        tool_calls_used: int,
        step_number: int,
        checkpoint_generation: int,
    ) -> AgentLoopOutcome:
        """Run from either the initial Session or a validated recovery point."""

        while True:
            self._trace.append(
                Step(
                    step_number=step_number,
                    purpose="Request the next model decision.",
                )
            )
            try:
                decision = self._model.decide(model_input)
            except ModelBoundaryError as error:
                reason = error.error_type
                if not self._recorded_model_error_matches(reason):
                    reason = "harness_invariant_error"
                return self._finish_failure(
                    reason,
                    state=state,
                    tool_calls_used=tool_calls_used,
                    checkpoint_generation=checkpoint_generation,
                )

            if not isinstance(decision, (FinalAnswerDecision, ToolCallDecision)):
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
                    checkpoint_generation=checkpoint_generation,
                )
            if not self._recorded_model_success_matches(decision):
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
                    checkpoint_generation=checkpoint_generation,
                )

            if isinstance(decision, FinalAnswerDecision):
                canonical_evidence = tuple(
                    citation.to_canonical() for citation in decision.evidence
                )
                evidence_validation = validate_final_answer_evidence(
                    canonical_evidence,
                    events=self._trace.events,
                    task_id=self._trace.task_id,
                )
                if not evidence_validation.is_valid:
                    return self._finish_failure(
                        "insufficient_evidence",
                        state=state,
                        tool_calls_used=tool_calls_used,
                        checkpoint_generation=checkpoint_generation,
                    )
                recorded_final = self._trace.finalize(
                    FinalAnswer(
                        answer=decision.answer,
                        evidence=list(canonical_evidence),
                        uncertainties=list(decision.uncertainties),
                        next_queries=list(decision.next_queries),
                        termination_reason="completed",
                    )
                )
                outcome = AgentLoopOutcome(
                    final_answer=recorded_final.model_copy(deep=True),
                    final_state=state.model_copy(deep=True),
                    tool_calls_used=tool_calls_used,
                )
                self._mark_checkpoint_completed(
                    outcome,
                    checkpoint_generation=checkpoint_generation,
                )
                return outcome

            if (
                tool_calls_used >= MAX_TOOL_CALLS_PER_TASK
                or state.remaining_tool_calls == 0
            ):
                return self._finish_failure(
                    "tool_budget_exhausted",
                    state=state,
                    tool_calls_used=tool_calls_used,
                    checkpoint_generation=checkpoint_generation,
                )

            tool_calls_used += 1
            try:
                outcome = await self._tool_step_executor.execute(state, decision)
            except ToolStepInvariantError:
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
                    checkpoint_generation=checkpoint_generation,
                )

            if not self._valid_tool_outcome(state, decision, outcome):
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
                    checkpoint_generation=checkpoint_generation,
                )

            tool_result = outcome.tool_result
            if tool_result is None:  # Narrowed by _valid_tool_outcome.
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
                    checkpoint_generation=checkpoint_generation,
                )

            state = outcome.next_state
            if tool_result.status == "error":
                reason: FailureTerminationReason
                if tool_result.error_type == ToolErrorCode.TOOL_TIMEOUT.value:
                    reason = "tool_timeout"
                else:
                    reason = "tool_error"
                return self._finish_failure(
                    reason,
                    state=state,
                    tool_calls_used=tool_calls_used,
                    checkpoint_generation=checkpoint_generation,
                )

            model_input = outcome.next_model_input
            step_number += 1
            if self._checkpoint_store is not None:
                checkpoint_generation += 1
                checkpoint = ResumableCheckpoint(
                    task_id=self._trace.task_id,
                    generation=checkpoint_generation,
                    state=state.model_copy(deep=True),
                    next_model_input=model_input.model_copy(deep=True),
                    tool_calls_used=tool_calls_used,
                    remaining_tool_calls=state.remaining_tool_calls,
                    next_step_number=step_number,
                    trace=self._trace.to_dict(),
                )
                self._checkpoint_store.save(checkpoint)
                if self._on_checkpoint_saved is not None:
                    self._on_checkpoint_saved(checkpoint.model_copy(deep=True))

    def _recorded_model_success_matches(
        self,
        decision: FinalAnswerDecision | ToolCallDecision,
    ) -> bool:
        events = self._trace.events
        if len(events) < 2:
            return False
        request, result = events[-2:]
        return (
            isinstance(request, ModelRequest)
            and isinstance(result, ModelResult)
            and request.request_id == result.request_id
            and result.status == "success"
            and result.decision == model_decision_to_trace_payload(decision)
        )

    def _recorded_model_error_matches(self, error_type: ModelErrorType) -> bool:
        events = self._trace.events
        if len(events) < 2:
            return False
        request, result = events[-2:]
        return (
            isinstance(request, ModelRequest)
            and isinstance(result, ModelResult)
            and request.request_id == result.request_id
            and result.status == "error"
            and result.error_type == error_type
        )

    def _valid_tool_outcome(
        self,
        previous_state: ContextState,
        decision: ToolCallDecision,
        outcome: object,
    ) -> bool:
        if not isinstance(outcome, ToolStepOutcome):
            return False
        if outcome.tool_result is None:
            return False
        if not isinstance(outcome.next_state, ContextState):
            return False
        if not isinstance(outcome.next_model_input, ModelInput):
            return False
        if (
            outcome.next_state.remaining_tool_calls
            != previous_state.remaining_tool_calls - 1
        ):
            return False
        if (
            outcome.next_model_input.remaining_tool_calls
            != outcome.next_state.remaining_tool_calls
        ):
            return False
        if outcome.tool_result.error_type not in {
            None,
            *(error_code.value for error_code in ToolErrorCode),
        }:
            return False

        events = self._trace.events
        if len(events) < 2:
            return False
        recorded_call, recorded_result = events[-2:]
        return (
            isinstance(recorded_call, ToolCall)
            and isinstance(recorded_result, ToolResult)
            and recorded_call.call_id == decision.call_id
            and recorded_call.tool_name == decision.tool_name
            and recorded_result.call_id == recorded_call.call_id
            and recorded_result == outcome.tool_result
        )

    def _finish_failure(
        self,
        reason: FailureTerminationReason,
        *,
        state: ContextState,
        tool_calls_used: int,
        checkpoint_generation: int,
    ) -> AgentLoopOutcome:
        recorded_final = self._trace.finalize(
            build_failure_final_answer(reason)
        )
        outcome = AgentLoopOutcome(
            final_answer=recorded_final.model_copy(deep=True),
            final_state=state.model_copy(deep=True),
            tool_calls_used=tool_calls_used,
        )
        self._mark_checkpoint_completed(
            outcome,
            checkpoint_generation=checkpoint_generation,
        )
        return outcome

    def _mark_checkpoint_completed(
        self,
        outcome: AgentLoopOutcome,
        *,
        checkpoint_generation: int,
    ) -> None:
        """Atomically replace an existing resumable generation after terminal."""

        if self._checkpoint_store is None or checkpoint_generation == 0:
            return
        self._checkpoint_store.save(
            CompletedCheckpoint(
                task_id=self._trace.task_id,
                generation=checkpoint_generation + 1,
                final_state=outcome.final_state.model_copy(deep=True),
                tool_calls_used=outcome.tool_calls_used,
                remaining_tool_calls=outcome.final_state.remaining_tool_calls,
                trace=self._trace.to_dict(),
            )
        )
