"""Map grounded incident fields into deterministic offline retrieval context.

The caller supplies an already-redacted :class:`IncidentInput`, an extraction
result, explicit repository selection, and every :class:`ContextState` policy
value.  This module distrusts the result's class name and repeats the existing
exact provenance gate before it creates any task or context.  It constructs
typed ``search_code`` candidates and source facts only; it never executes a
tool, calls a model, trims evidence, or enters the agent loop.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .context import ContextState, Evidence, EvidenceKind, RepositoryHint
from .incident_extraction import (
    IncidentExtractionCandidate,
    IncidentExtractionResult,
    IncidentExtractionValidationError,
    IncidentInput,
    SourcedIncidentValue,
    validate_incident_extraction,
)
from .tool_router import SearchCodeArguments


IncidentRetrievalField: TypeAlias = Literal[
    "method",
    "exception_class",
    "configuration_key",
    "error_text",
    "service_name",
]
StrictTaskText: TypeAlias = Annotated[str, Field(strict=True, min_length=1)]


class IncidentContextValidationError(ValueError):
    """The extraction result or explicit context selection is not trustworthy."""


class IncidentRetrievalTask(BaseModel):
    """One ordered, source-linked, literal ``search_code`` candidate.

    ``query`` deliberately duplicates ``arguments.query`` so callers can
    inspect the incident-field contract without converting the tool model back
    into a free-form mapping.  The validator keeps those two views identical
    and freezes the first-version literal-search policy.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    field_name: IncidentRetrievalField
    query: StrictTaskText
    source_ids: tuple[StrictTaskText, ...] = Field(min_length=1)
    arguments: SearchCodeArguments

    @model_validator(mode="after")
    def validate_literal_search_contract(self) -> "IncidentRetrievalTask":
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids must be unique")
        if self.arguments.query != self.query:
            raise ValueError("arguments.query must exactly match query")
        if self.arguments.literal is not True:
            raise ValueError("incident retrieval queries must be literal")
        if self.arguments.lang is not None or self.arguments.path is not None:
            raise ValueError("incident retrieval must not guess lang or path")
        return self

    def to_payload(self) -> dict[str, Any]:
        """Return a detached JSON-compatible payload in declared field order."""

        return self.model_dump(mode="json")

    def to_json(self) -> str:
        """Return byte-stable UTF-8-safe JSON without runtime metadata."""

        return json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )


def map_incident_to_retrieval_context(
    incident_input: IncidentInput,
    extraction_result: IncidentExtractionResult,
    *,
    system_instruction: str,
    repository_hints: Sequence[RepositoryHint],
    selected_repository: str | None,
    evidence_item_budget: int,
    remaining_tool_calls: int,
) -> tuple[tuple[IncidentRetrievalTask, ...], ContextState]:
    """Return detached ordered retrieval tasks and their deterministic state.

    ``selected_repository`` is required even when its value is ``None``.  A
    non-null value must exactly equal one supplied canonical name; aliases,
    case changes, basenames, URLs, service names, and other inferred values are
    never resolved here.  The full task list is independent of
    ``remaining_tool_calls`` and is serialized into ``current_task`` so its
    order, field names, original queries, provenance, and typed arguments are
    recoverable without a natural-language parser.
    """

    if not isinstance(incident_input, IncidentInput):
        raise TypeError("incident_input must be an IncidentInput")
    if not isinstance(extraction_result, IncidentExtractionResult):
        raise TypeError("extraction_result must be an IncidentExtractionResult")
    if not isinstance(repository_hints, Sequence) or isinstance(
        repository_hints,
        (str, bytes, bytearray),
    ):
        raise TypeError("repository_hints must be a sequence of RepositoryHint")
    if selected_repository is not None and not isinstance(
        selected_repository,
        str,
    ):
        raise TypeError("selected_repository must be a string or None")

    input_snapshot = incident_input.model_copy(deep=True)
    result_snapshot = extraction_result.model_copy(deep=True)
    validated_result = _revalidate_result(input_snapshot, result_snapshot)

    supplied_hints = tuple(repository_hints)
    if not all(isinstance(hint, RepositoryHint) for hint in supplied_hints):
        raise TypeError("repository_hints must contain RepositoryHint instances")
    hint_snapshot = tuple(
        hint.model_copy(deep=True) for hint in supplied_hints
    )
    canonical_names = tuple(hint.canonical_name for hint in hint_snapshot)
    if len(canonical_names) != len(set(canonical_names)):
        raise IncidentContextValidationError(
            "repository_hints must have unique canonical names"
        )
    if (
        selected_repository is not None
        and selected_repository not in canonical_names
    ):
        raise IncidentContextValidationError(
            "selected_repository must exactly match a canonical repository hint"
        )

    tasks = _build_tasks(validated_result, selected_repository)
    current_task = json.dumps(
        {
            "task_type": "incident_retrieval",
            "tasks": [task.to_payload() for task in tasks],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    evidence = tuple(
        Evidence(
            kind=EvidenceKind.FACT,
            source=(
                f"incident:{source.source_type}:{source.source_id}"
            ),
            content=source.text,
        )
        for source in input_snapshot.sources
    )

    try:
        state = ContextState.model_validate(
            {
                "system_instruction": system_instruction,
                "current_task": current_task,
                "repository_hints": hint_snapshot,
                "evidence": evidence,
                "evidence_item_budget": evidence_item_budget,
                "remaining_tool_calls": remaining_tool_calls,
            },
            strict=True,
        )
    except ValidationError as exc:
        raise IncidentContextValidationError(
            "explicit context inputs do not match ContextState"
        ) from exc

    return tasks, state


def _revalidate_result(
    incident_input: IncidentInput,
    extraction_result: IncidentExtractionResult,
) -> IncidentExtractionResult:
    """Downgrade a result to an untrusted candidate and reuse the source gate."""

    try:
        candidate = IncidentExtractionCandidate.model_validate(
            extraction_result.model_dump(mode="python", warnings=False),
            strict=True,
        )
        return validate_incident_extraction(incident_input, candidate)
    except (IncidentExtractionValidationError, ValidationError) as exc:
        raise IncidentContextValidationError(
            "extraction_result failed exact source revalidation"
        ) from exc


def _build_tasks(
    result: IncidentExtractionResult,
    repository: str | None,
) -> tuple[IncidentRetrievalTask, ...]:
    observations: list[tuple[IncidentRetrievalField, SourcedIncidentValue]] = []
    if result.method is not None:
        observations.append(("method", result.method))
    if result.exception_class is not None:
        observations.append(("exception_class", result.exception_class))
    observations.extend(
        ("configuration_key", configuration_key)
        for configuration_key in result.configuration_keys
    )
    if result.error_text is not None:
        observations.append(("error_text", result.error_text))
    if result.service_name is not None:
        observations.append(("service_name", result.service_name))

    return tuple(
        IncidentRetrievalTask(
            field_name=field_name,
            query=observation.value,
            source_ids=tuple(observation.source_ids),
            arguments=SearchCodeArguments(
                query=observation.value,
                repo=repository,
                lang=None,
                path=None,
                literal=True,
            ),
        )
        for field_name, observation in observations
    )
