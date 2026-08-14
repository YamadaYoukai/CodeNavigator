"""Deterministic termination orchestration for the code-understanding agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

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
from .model_boundary import FinalAnswerDecision, ModelClient, ToolCallDecision
from .model_errors import ModelBoundaryError, ModelErrorType
from .tool_router import GET_FILE_CONTEXT, SEARCH_CODE, ToolErrorCode
from .tool_step import (
    ToolStepExecutor,
    ToolStepInvariantError,
    ToolStepOutcome,
)
from .trace import TraceRecorder


MAX_TOOL_CALLS_PER_TASK = 6

FailureTerminationReason: TypeAlias = Literal[
    "tool_budget_exhausted",
    "model_execution_error",
    "invalid_model_output",
    "tool_error",
    "model_timeout",
    "tool_timeout",
    "harness_invariant_error",
]

_FAILURE_ANSWERS: dict[FailureTerminationReason, str] = {
    "tool_budget_exhausted": "The tool-call budget was exhausted.",
    "model_execution_error": "Model execution failed.",
    "invalid_model_output": "The model returned an invalid decision.",
    "tool_error": "Tool execution failed.",
    "model_timeout": "Model execution timed out.",
    "tool_timeout": "Tool execution timed out.",
    "harness_invariant_error": "An agent harness invariant failed.",
}


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
    ) -> None:
        if not isinstance(context_builder, ContextBuilder):
            raise TypeError("context_builder must be a ContextBuilder")
        if not callable(getattr(model, "decide", None)):
            raise TypeError("model must provide decide(model_input)")
        if not isinstance(tool_step_executor, ToolStepExecutor):
            raise TypeError("tool_step_executor must be a ToolStepExecutor")
        if not isinstance(trace, TraceRecorder):
            raise TypeError("trace must be a TraceRecorder")

        self._context_builder = context_builder
        self._model = model
        self._tool_step_executor = tool_step_executor
        self._trace = trace

    async def run(self, initial_state: ContextState) -> AgentLoopOutcome:
        """Run sequential decisions until one success or stable failure terminal."""

        if not isinstance(initial_state, ContextState):
            raise TypeError("initial_state must be a ContextState")
        if self._trace.events or self._trace.is_finalized:
            raise ValueError("trace must be empty and unfinalized")

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
                )

            if not isinstance(decision, (FinalAnswerDecision, ToolCallDecision)):
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
                )
            if not self._recorded_model_success_matches(decision):
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
                )

            if isinstance(decision, FinalAnswerDecision):
                recorded_final = self._trace.finalize(
                    FinalAnswer(
                        answer=decision.answer,
                        evidence=list(decision.evidence),
                        uncertainties=list(decision.uncertainties),
                        next_queries=list(decision.next_queries),
                        termination_reason="completed",
                    )
                )
                return AgentLoopOutcome(
                    final_answer=recorded_final.model_copy(deep=True),
                    final_state=state.model_copy(deep=True),
                    tool_calls_used=tool_calls_used,
                )

            if (
                tool_calls_used >= MAX_TOOL_CALLS_PER_TASK
                or state.remaining_tool_calls == 0
            ):
                return self._finish_failure(
                    "tool_budget_exhausted",
                    state=state,
                    tool_calls_used=tool_calls_used,
                )

            tool_calls_used += 1
            try:
                outcome = await self._tool_step_executor.execute(state, decision)
            except ToolStepInvariantError:
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
                )

            if not self._valid_tool_outcome(state, decision, outcome):
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
                )

            tool_result = outcome.tool_result
            if tool_result is None:  # Narrowed by _valid_tool_outcome.
                return self._finish_failure(
                    "harness_invariant_error",
                    state=state,
                    tool_calls_used=tool_calls_used,
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
                )

            model_input = outcome.next_model_input
            step_number += 1

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
            and result.decision == decision.model_dump(mode="json")
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
    ) -> AgentLoopOutcome:
        recorded_final = self._trace.finalize(
            FinalAnswer(
                answer=_FAILURE_ANSWERS[reason],
                evidence=[],
                uncertainties=[],
                next_queries=[],
                termination_reason=reason,
            )
        )
        return AgentLoopOutcome(
            final_answer=recorded_final.model_copy(deep=True),
            final_state=state.model_copy(deep=True),
            tool_calls_used=tool_calls_used,
        )
