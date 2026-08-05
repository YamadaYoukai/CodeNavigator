"""Deterministic model-input construction for the code-understanding agent.

This module only turns already-known state into a stable model-input payload.
It does not call a model or a tool, and it does not own an execution loop,
timeout, retry, memory, or persistence policy.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .tool_router import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    GetFileContextArguments,
    SearchCodeArguments,
)


class EvidenceKind(str, Enum):
    """The two evidence classes used by the deterministic trimming policy."""

    FACT = "fact"
    HISTORY = "history"


class Evidence(BaseModel):
    """An atomic item of known context supplied to the builder.

    ``fact`` represents a verified observation, such as a tool result or a
    source location. ``history`` represents lower-value prior narration.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: EvidenceKind
    source: str = Field(min_length=1)
    content: str = Field(min_length=1)


class RepositoryHint(BaseModel):
    """One canonical indexed repository name and its exact external aliases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_names(self) -> "RepositoryHint":
        if (
            not self.canonical_name.strip()
            or self.canonical_name != self.canonical_name.strip()
        ):
            raise ValueError("canonical repository name must be trimmed and non-empty")
        if any(not alias.strip() or alias != alias.strip() for alias in self.aliases):
            raise ValueError("repository aliases must be trimmed non-empty strings")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("repository aliases must be unique within one hint")
        return self


class ContextState(BaseModel):
    """All caller-owned inputs needed to build one deterministic payload.

    ``evidence_item_budget`` is an item count rather than a token estimate so
    the contract remains independent of a model or tokenizer. It controls
    construction-time trimming only and is not a tool-call budget.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_instruction: str = Field(min_length=1)
    current_task: str = Field(min_length=1)
    repository_hints: tuple[RepositoryHint, ...] = Field(default_factory=tuple)
    evidence: tuple[Evidence, ...] = Field(default_factory=tuple)
    evidence_item_budget: int = Field(ge=0)
    remaining_tool_calls: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_repository_hints(self) -> "ContextState":
        repository_names = tuple(
            hint.canonical_name for hint in self.repository_hints
        )
        if len(set(repository_names)) != len(repository_names):
            raise ValueError("repository_hints must have unique canonical names")
        return self


class ToolSchema(BaseModel):
    """The public, model-facing form of one allowed tool contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["search_code", "get_file_context"]
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]


class ModelInput(BaseModel):
    """Stable payload passed to a future model integration boundary.

    The field declaration order is the serialization order. Consumers can call
    :meth:`to_json` when they need a repeatable wire representation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_instruction: str = Field(min_length=1)
    current_task: str = Field(min_length=1)
    repository_hints: tuple[RepositoryHint, ...]
    tool_schemas: tuple[ToolSchema, ...]
    evidence: tuple[Evidence, ...]
    remaining_tool_calls: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_tool_allowlist(self) -> "ModelInput":
        repository_names = tuple(
            hint.canonical_name for hint in self.repository_hints
        )
        if len(set(repository_names)) != len(repository_names):
            raise ValueError("repository_hints must have unique canonical names")

        names = tuple(schema.name for schema in self.tool_schemas)
        expected_names = (SEARCH_CODE, GET_FILE_CONTEXT)
        if names != expected_names:
            raise ValueError(
                "tool_schemas must contain search_code then get_file_context"
            )
        return self

    def to_payload(self) -> dict[str, Any]:
        """Return a detached JSON-compatible payload in contract field order."""

        return self.model_dump(mode="json")

    def to_json(self) -> str:
        """Serialize the payload without timestamps or key-order randomness."""

        return json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _canonicalize_json(value: Any) -> JsonValue:
    """Copy JSON-compatible values with deterministic mapping-key order."""

    if isinstance(value, dict):
        return {
            str(key): _canonicalize_json(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return [_canonicalize_json(item) for item in value]
    return value


_TOOL_SCHEMAS: tuple[ToolSchema, ToolSchema] = (
    ToolSchema(
        name=SEARCH_CODE,
        description="Search indexed source code.",
        input_schema=_canonicalize_json(SearchCodeArguments.model_json_schema()),
    ),
    ToolSchema(
        name=GET_FILE_CONTEXT,
        description="Read source lines around a known repository location.",
        input_schema=_canonicalize_json(GetFileContextArguments.model_json_schema()),
    ),
)

_EVIDENCE_PRIORITY: dict[EvidenceKind, int] = {
    EvidenceKind.FACT: 0,
    EvidenceKind.HISTORY: 1,
}


class ContextBuilder:
    """Build a stable model input from caller-provided state only.

    The builder has no mutable state. It stable-sorts evidence by the documented
    value tier and returns fresh tool-schema copies, leaving ``ContextState``
    untouched.
    """

    def build(
        self,
        state: ContextState,
        *,
        protected_evidence: Iterable[Evidence] = (),
    ) -> ModelInput:
        """Build one model input without invoking tools or external services.

        ``protected_evidence`` is a narrow escape hatch for fresh observations
        that must reach the immediately following model call. At most one item
        may be protected, and it must already occur in ``state.evidence``. The
        protected item consumes the ordinary evidence budget first, but is
        still retained when that budget is zero so a just-produced tool result
        cannot disappear at the state-to-model boundary.
        """

        selected_evidence = self._select_evidence(state, protected_evidence)

        return ModelInput(
            system_instruction=state.system_instruction,
            current_task=state.current_task,
            repository_hints=tuple(
                hint.model_copy(deep=True) for hint in state.repository_hints
            ),
            tool_schemas=tuple(
                schema.model_copy(deep=True) for schema in _TOOL_SCHEMAS
            ),
            evidence=selected_evidence,
            remaining_tool_calls=state.remaining_tool_calls,
        )

    @staticmethod
    def _select_evidence(
        state: ContextState,
        protected_evidence: Iterable[Evidence],
    ) -> tuple[Evidence, ...]:
        protected_items = tuple(protected_evidence)
        if not all(isinstance(item, Evidence) for item in protected_items):
            raise TypeError("protected_evidence must contain Evidence instances")
        if len(protected_items) > 1:
            raise ValueError("at most one protected evidence item is allowed")

        unmatched_indexes = list(range(len(state.evidence)))
        protected_indexes: list[int] = []
        for protected_item in protected_items:
            matching_index = next(
                (
                    index
                    for index in unmatched_indexes
                    if state.evidence[index] == protected_item
                ),
                None,
            )
            if matching_index is None:
                raise ValueError("protected evidence must already occur in state")
            protected_indexes.append(matching_index)
            unmatched_indexes.remove(matching_index)

        ranked_indexes = sorted(
            unmatched_indexes,
            key=lambda index: (
                _EVIDENCE_PRIORITY[state.evidence[index].kind],
                index,
            ),
        )
        remaining_capacity = max(
            state.evidence_item_budget - len(protected_indexes),
            0,
        )
        selected_indexes = (
            *protected_indexes,
            *ranked_indexes[:remaining_capacity],
        )
        return tuple(state.evidence[index] for index in selected_indexes)


def build_context(state: ContextState) -> ModelInput:
    """Build a standalone deterministic model input with the default builder."""

    return ContextBuilder().build(state)
