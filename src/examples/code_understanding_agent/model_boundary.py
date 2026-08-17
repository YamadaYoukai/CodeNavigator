"""Typed, deterministic boundary for model decisions.

The production-facing contract is deliberately small: a ``ModelClient``
consumes one complete ``ModelInput`` and returns one validated
``ModelDecision``.  ``FakeModel`` implements the same contract with a scripted
decision sequence for tests and local examples.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .context import ModelInput
from .tool_router import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    GetFileContextArguments,
    SearchCodeArguments,
)


NonEmptyText: TypeAlias = Annotated[str, Field(min_length=1)]
StrictPositiveInt: TypeAlias = Annotated[int, Field(strict=True, ge=1)]


class ToolCallDecision(BaseModel):
    """Ask the caller to execute one of the two supported retrieval tools."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["tool_call"] = "tool_call"
    call_id: NonEmptyText
    tool_name: Literal["search_code", "get_file_context"]
    arguments: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_tool_arguments(self) -> "ToolCallDecision":
        """Validate arguments with the shared contract for the selected tool."""

        if self.tool_name == SEARCH_CODE:
            SearchCodeArguments.model_validate(self.arguments)
        elif self.tool_name == GET_FILE_CONTEXT:
            GetFileContextArguments.model_validate(self.arguments)
        return self


class FinalAnswerCitation(BaseModel):
    """One exact source line submitted through the model boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repo: NonEmptyText
    path: NonEmptyText
    line: StrictPositiveInt

    @field_validator("repo", "path")
    @classmethod
    def validate_source_component(cls, value: str) -> str:
        """Reject ambiguous or non-portable repository/path components."""

        if value != value.strip():
            raise ValueError("source component must not have surrounding whitespace")
        if value.startswith("/") or value.endswith("/"):
            raise ValueError("source component must be relative")
        if any(character in value for character in (":", "\\", "\n", "\r")):
            raise ValueError("source component contains a forbidden character")
        if any(part in {"", ".", ".."} for part in value.split("/")):
            raise ValueError("source component contains an invalid path segment")
        return value

    def to_canonical(self) -> str:
        """Return the existing public ``repo/path:line`` representation."""

        return f"{self.repo}/{self.path}:{self.line}"


class FinalAnswerDecision(BaseModel):
    """Return the terminal, evidence-aware answer to the current task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["final_answer"] = "final_answer"
    answer: NonEmptyText
    evidence: tuple[FinalAnswerCitation, ...] = Field(default_factory=tuple)
    uncertainties: tuple[NonEmptyText, ...] = Field(default_factory=tuple)
    next_queries: tuple[NonEmptyText, ...] = Field(default_factory=tuple)


ModelDecision: TypeAlias = Annotated[
    ToolCallDecision | FinalAnswerDecision,
    Field(discriminator="decision_type"),
]


def model_decision_to_trace_payload(
    decision: ToolCallDecision | FinalAnswerDecision,
) -> dict[str, JsonValue]:
    """Serialize a decision while preserving the public citation format."""

    if not isinstance(decision, (ToolCallDecision, FinalAnswerDecision)):
        raise TypeError("decision must be a model decision")
    payload = decision.model_dump(mode="json")
    if isinstance(decision, FinalAnswerDecision):
        payload["evidence"] = [
            citation.to_canonical() for citation in decision.evidence
        ]
    return payload


class ModelClient(Protocol):
    """Model adapter contract used by the future agent execution loop."""

    def decide(self, model_input: ModelInput) -> ModelDecision:
        """Return exactly one decision for the supplied model input."""
        ...


class FakeModel:
    """Return a caller-provided decision script without external side effects.

    Both scripted decisions and received inputs are deep-copied at their
    boundaries.  This keeps the fake deterministic even though JSON mappings
    nested inside frozen Pydantic models remain ordinary mutable containers.
    """

    def __init__(self, decisions: Iterable[ModelDecision]) -> None:
        scripted_decisions = tuple(decisions)
        if not all(
            isinstance(decision, (ToolCallDecision, FinalAnswerDecision))
            for decision in scripted_decisions
        ):
            raise TypeError(
                "decisions must contain only ToolCallDecision or "
                "FinalAnswerDecision instances"
            )

        self._decisions = tuple(
            decision.model_copy(deep=True) for decision in scripted_decisions
        )
        self._next_decision = 0
        self._model_inputs: list[ModelInput] = []

    @property
    def model_inputs(self) -> tuple[ModelInput, ...]:
        """Return detached snapshots of inputs accepted by successful calls."""

        return tuple(
            model_input.model_copy(deep=True) for model_input in self._model_inputs
        )

    @property
    def remaining_decisions(self) -> int:
        """Return the number of unconsumed scripted decisions."""

        return len(self._decisions) - self._next_decision

    def decide(self, model_input: ModelInput) -> ModelDecision:
        """Return the next scripted decision or fail on script exhaustion."""

        if not isinstance(model_input, ModelInput):
            raise TypeError("model_input must be a ModelInput")
        if self._next_decision >= len(self._decisions):
            raise RuntimeError("fake model has no scripted decisions remaining")

        decision = self._decisions[self._next_decision]
        self._next_decision += 1
        self._model_inputs.append(model_input.model_copy(deep=True))
        return decision.model_copy(deep=True)
