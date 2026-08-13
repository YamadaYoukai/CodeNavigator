"""Minimal successful-path orchestration for the code-understanding agent."""

from __future__ import annotations

from dataclasses import dataclass

from .context import ContextBuilder, ContextState
from .events import FinalAnswer, Session, Step
from .model_boundary import FinalAnswerDecision, ModelClient, ToolCallDecision
from .tool_router import GET_FILE_CONTEXT, SEARCH_CODE
from .tool_step import ToolStepExecutor
from .trace import TraceRecorder


MAX_TOOL_CALLS_PER_TASK = 6


@dataclass(frozen=True, slots=True)
class AgentLoopOutcome:
    """Detached outputs of one completed agent loop."""

    final_answer: FinalAnswer
    final_state: ContextState
    tool_calls_used: int


class AgentLoop:
    """Orchestrate the existing model and single-tool-step boundaries.

    This first version intentionally supports only the deterministic success
    path. The frozen failure and timeout terminal semantics live in DESIGN.md
    and will be implemented as separate changes.
    """

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
        """Run tool decisions sequentially until one final answer is returned."""

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
            decision = self._model.decide(model_input)

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

            if not isinstance(decision, ToolCallDecision):
                raise RuntimeError(
                    "successful-path agent loop received an unsupported decision"
                )
            if (
                tool_calls_used >= MAX_TOOL_CALLS_PER_TASK
                or state.remaining_tool_calls == 0
            ):
                raise RuntimeError(
                    "tool-budget termination is not implemented in the "
                    "successful-path agent loop"
                )

            outcome = await self._tool_step_executor.execute(state, decision)
            if outcome.tool_result is None:
                raise RuntimeError("tool step did not execute despite available budget")

            tool_calls_used += 1
            if outcome.tool_result.status != "success":
                raise RuntimeError(
                    "tool-error termination is not implemented in the "
                    "successful-path agent loop"
                )

            state = outcome.next_state
            model_input = outcome.next_model_input
            step_number += 1
