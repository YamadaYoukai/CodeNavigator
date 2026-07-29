"""Deterministic model-input construction for the code-understanding agent.

This module only turns already-known state into a stable model-input payload.
It does not call a model or a tool, and it does not own an execution loop,
timeout, retry, memory, or persistence policy.
"""

from __future__ import annotations

import json
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


class ContextState(BaseModel):
    """All caller-owned inputs needed to build one deterministic payload.

    ``evidence_item_budget`` is an item count rather than a token estimate so
    the contract remains independent of a model or tokenizer. It controls
    construction-time trimming only and is not a tool-call budget.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_instruction: str = Field(min_length=1)
    current_task: str = Field(min_length=1)
    evidence: tuple[Evidence, ...] = Field(default_factory=tuple)
    evidence_item_budget: int = Field(ge=0)
    remaining_tool_calls: int = Field(ge=0)


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
    tool_schemas: tuple[ToolSchema, ...]
    evidence: tuple[Evidence, ...]
    remaining_tool_calls: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_tool_allowlist(self) -> "ModelInput":
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

    def build(self, state: ContextState) -> ModelInput:
        """Build one model input without invoking tools or external services."""

        selected_evidence = tuple(
            evidence
            for _, evidence in sorted(
                enumerate(state.evidence),
                key=lambda indexed: (
                    _EVIDENCE_PRIORITY[indexed[1].kind],
                    indexed[0],
                ),
            )[: state.evidence_item_budget]
        )

        return ModelInput(
            system_instruction=state.system_instruction,
            current_task=state.current_task,
            tool_schemas=tuple(
                schema.model_copy(deep=True) for schema in _TOOL_SCHEMAS
            ),
            evidence=selected_evidence,
            remaining_tool_calls=state.remaining_tool_calls,
        )


def build_context(state: ContextState) -> ModelInput:
    """Build a standalone deterministic model input with the default builder."""

    return ContextBuilder().build(state)
