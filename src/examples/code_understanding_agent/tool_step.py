"""One provider-independent tool-call state transition.

This boundary deliberately executes exactly one already-validated model
decision. It does not call a model, retry a tool, or own an agent loop.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from .context import ContextBuilder, ContextState, Evidence, EvidenceKind, ModelInput
from .events import ToolCall, ToolResult
from .model_boundary import ToolCallDecision


class ToolCallExecutor(Protocol):
    """The part of :class:`ToolRouter` required by one transition."""

    def execute(self, call: ToolCall) -> Awaitable[ToolResult]:
        """Execute and classify one tool call."""
        ...


@dataclass(frozen=True, slots=True)
class ToolStepOutcome:
    """Outputs of one attempted tool transition.

    ``tool_result`` is ``None`` only when the input tool-call budget is already
    exhausted. In that case ``next_state`` is the original state object.
    """

    tool_result: ToolResult | None
    next_state: ContextState
    next_model_input: ModelInput


class ToolStepExecutor:
    """Turn one tool decision into one result, state, and next model input."""

    def __init__(
        self,
        *,
        router: ToolCallExecutor,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self._router = router
        self._context_builder = context_builder or ContextBuilder()

    async def execute(
        self,
        state: ContextState,
        decision: ToolCallDecision,
    ) -> ToolStepOutcome:
        """Execute at most one tool call and consume at most one call budget."""

        if not isinstance(state, ContextState):
            raise TypeError("state must be a ContextState")
        if not isinstance(decision, ToolCallDecision):
            raise TypeError("decision must be a ToolCallDecision")

        if state.remaining_tool_calls == 0:
            return ToolStepOutcome(
                tool_result=None,
                next_state=state,
                next_model_input=self._context_builder.build(state),
            )

        call = ToolCall(
            call_id=decision.call_id,
            tool_name=decision.tool_name,
            arguments=deepcopy(decision.arguments),
        )
        tool_result = await self._router.execute(call)
        if not isinstance(tool_result, ToolResult):
            raise TypeError("router.execute must return a ToolResult")
        if tool_result.call_id != decision.call_id:
            raise ValueError("tool result call_id must match the decision")

        tool_evidence = _to_fact_evidence(decision, tool_result)
        next_state = state.model_copy(
            update={
                # Fresh facts precede older facts so they remain preferred by
                # ordinary subsequent ContextBuilder calls as well.
                "evidence": (
                    tool_evidence,
                    *(item.model_copy(deep=True) for item in state.evidence),
                ),
                "remaining_tool_calls": state.remaining_tool_calls - 1,
            },
            deep=True,
        )
        fresh_evidence = next_state.evidence[0]
        next_model_input = self._context_builder.build(
            next_state,
            protected_evidence=(fresh_evidence,),
        )

        return ToolStepOutcome(
            tool_result=tool_result.model_copy(deep=True),
            next_state=next_state,
            next_model_input=next_model_input,
        )


def _to_fact_evidence(
    decision: ToolCallDecision,
    result: ToolResult,
) -> Evidence:
    if result.status == "success":
        content = {
            "result": result.result,
            "status": "success",
        }
    else:
        content = {
            "error_type": result.error_type,
            "status": "error",
        }

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
