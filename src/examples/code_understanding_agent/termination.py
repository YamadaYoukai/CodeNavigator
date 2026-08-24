"""Stable terminal-answer semantics shared by execution and persistence."""

from __future__ import annotations

from typing import Literal, TypeAlias

from .events import FinalAnswer


FailureTerminationReason: TypeAlias = Literal[
    "insufficient_evidence",
    "tool_budget_exhausted",
    "model_execution_error",
    "invalid_model_output",
    "tool_error",
    "model_timeout",
    "tool_timeout",
    "harness_invariant_error",
]

FAILURE_TERMINATION_REASONS = frozenset(
    {
        "insufficient_evidence",
        "tool_budget_exhausted",
        "model_execution_error",
        "invalid_model_output",
        "tool_error",
        "model_timeout",
        "tool_timeout",
        "harness_invariant_error",
    }
)

_FAILURE_ANSWERS: dict[FailureTerminationReason, str] = {
    "insufficient_evidence": (
        "Insufficient verified tool evidence is available to answer the task."
    ),
    "tool_budget_exhausted": "The tool-call budget was exhausted.",
    "model_execution_error": "Model execution failed.",
    "invalid_model_output": "The model returned an invalid decision.",
    "tool_error": "Tool execution failed.",
    "model_timeout": "Model execution timed out.",
    "tool_timeout": "Tool execution timed out.",
    "harness_invariant_error": "An agent harness invariant failed.",
}


def build_failure_final_answer(reason: FailureTerminationReason) -> FinalAnswer:
    """Build the one public terminal payload for a stable failure reason."""

    if reason not in FAILURE_TERMINATION_REASONS:
        raise ValueError("unsupported failure termination reason")
    return FinalAnswer(
        answer=_FAILURE_ANSWERS[reason],
        evidence=[],
        uncertainties=[],
        next_queries=[],
        termination_reason=reason,
    )
