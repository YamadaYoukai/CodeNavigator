"""Strict, offline boundary for source-grounded incident field extraction.

The caller owns redaction before constructing :class:`IncidentInput`.  This
module accepts only caller-supplied source lines, validates one scripted
structured candidate, and fails closed unless every non-null value occurs
verbatim in every source line it cites.  It does not call a model, infer a root
cause, or add evidence to agent context.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Literal, Protocol, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


StrictNonEmptyText: TypeAlias = Annotated[
    str,
    Field(strict=True, min_length=1),
]
IncidentSourceType: TypeAlias = Literal["description", "log", "stack_trace"]


class _StableJsonModel(BaseModel):
    """Frozen strict model with detached, deterministic JSON serialization."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

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


def _validate_source_id(value: str) -> str:
    """Reject source identities whose spelling is not already canonical."""

    if value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("source_id must not contain whitespace")
    return value


class IncidentSource(_StableJsonModel):
    """One already-redacted input line with a stable caller-owned identity."""

    source_id: StrictNonEmptyText
    source_type: IncidentSourceType
    text: StrictNonEmptyText

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        return _validate_source_id(value)

    @field_validator("text")
    @classmethod
    def validate_source_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source text must contain a non-whitespace character")
        if "\n" in value or "\r" in value:
            raise ValueError("each incident source must contain exactly one line")
        return value


class IncidentInput(_StableJsonModel):
    """Ordered, caller-redacted description, log, and stack-trace lines."""

    sources: tuple[IncidentSource, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_source_ids(self) -> "IncidentInput":
        source_ids = tuple(source.source_id for source in self.sources)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_id values must be unique")
        return self


class SourcedIncidentValue(_StableJsonModel):
    """One observed text value and every source line claimed to support it."""

    value: StrictNonEmptyText
    source_ids: tuple[StrictNonEmptyText, ...] = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def validate_observed_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("observed value must contain a non-whitespace character")
        return value

    @field_validator("source_ids")
    @classmethod
    def validate_source_ids(cls, source_ids: tuple[str, ...]) -> tuple[str, ...]:
        for source_id in source_ids:
            _validate_source_id(source_id)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_ids must be unique")
        return source_ids


class _IncidentExtractionFields(_StableJsonModel):
    """The five and only five observable incident output categories."""

    exception_class: SourcedIncidentValue | None
    method: SourcedIncidentValue | None
    error_text: SourcedIncidentValue | None
    service_name: SourcedIncidentValue | None
    configuration_keys: tuple[SourcedIncidentValue, ...]

    @model_validator(mode="after")
    def validate_unique_configuration_keys(self) -> "_IncidentExtractionFields":
        values = tuple(item.value for item in self.configuration_keys)
        if len(values) != len(set(values)):
            raise ValueError("configuration keys must be unique")
        return self


class IncidentExtractionCandidate(_IncidentExtractionFields):
    """Strict untrusted structured output returned by an extractor adapter."""


class IncidentExtractionResult(_IncidentExtractionFields):
    """Candidate that passed exact source association validation."""


class IncidentExtractionValidationError(ValueError):
    """A structured candidate is not exactly grounded in its cited sources."""


class IncidentFieldExtractor(Protocol):
    """Provider-independent structured incident extraction contract."""

    def extract(
        self,
        incident_input: IncidentInput,
    ) -> IncidentExtractionCandidate:
        """Return one strict candidate for an already-redacted input."""
        ...


class FakeIncidentFieldExtractor:
    """Return a detached candidate script without network or clock access."""

    def __init__(
        self,
        candidates: Iterable[IncidentExtractionCandidate],
    ) -> None:
        scripted_candidates = tuple(candidates)
        if not all(
            isinstance(candidate, IncidentExtractionCandidate)
            for candidate in scripted_candidates
        ):
            raise TypeError(
                "candidates must contain only IncidentExtractionCandidate instances"
            )
        self._candidates = tuple(
            candidate.model_copy(deep=True) for candidate in scripted_candidates
        )
        self._next_candidate = 0
        self._incident_inputs: list[IncidentInput] = []

    @property
    def incident_inputs(self) -> tuple[IncidentInput, ...]:
        """Return detached snapshots accepted by successful fake calls."""

        return tuple(
            incident_input.model_copy(deep=True)
            for incident_input in self._incident_inputs
        )

    @property
    def remaining_candidates(self) -> int:
        """Return the number of unconsumed scripted candidates."""

        return len(self._candidates) - self._next_candidate

    def extract(
        self,
        incident_input: IncidentInput,
    ) -> IncidentExtractionCandidate:
        """Return the next detached candidate or fail on script exhaustion."""

        if not isinstance(incident_input, IncidentInput):
            raise TypeError("incident_input must be an IncidentInput")
        if self._next_candidate >= len(self._candidates):
            raise RuntimeError("fake extractor has no scripted candidates remaining")

        candidate = self._candidates[self._next_candidate]
        self._next_candidate += 1
        self._incident_inputs.append(incident_input.model_copy(deep=True))
        return candidate.model_copy(deep=True)


def validate_incident_extraction(
    incident_input: IncidentInput,
    candidate: IncidentExtractionCandidate,
) -> IncidentExtractionResult:
    """Validate exact provenance and return a detached trusted result.

    A value is supported only when its spelling, case, and Unicode code points
    occur as one contiguous substring in every cited source line.  Source IDs
    are resolved by identity, never by input position or fuzzy matching.
    """

    if not isinstance(incident_input, IncidentInput):
        raise TypeError("incident_input must be an IncidentInput")
    if not isinstance(candidate, IncidentExtractionCandidate):
        raise TypeError("candidate must be an IncidentExtractionCandidate")

    input_snapshot = incident_input.model_copy(deep=True)
    candidate_snapshot = candidate.model_copy(deep=True)
    source_by_id = {
        source.source_id: source for source in input_snapshot.sources
    }

    observations: list[tuple[str, SourcedIncidentValue]] = []
    for field_name in (
        "exception_class",
        "method",
        "error_text",
        "service_name",
    ):
        observation = getattr(candidate_snapshot, field_name)
        if observation is not None:
            observations.append((field_name, observation))
    observations.extend(
        (f"configuration_keys[{index}]", observation)
        for index, observation in enumerate(candidate_snapshot.configuration_keys)
    )

    for field_name, observation in observations:
        for source_id in observation.source_ids:
            source = source_by_id.get(source_id)
            if source is None:
                raise IncidentExtractionValidationError(
                    f"{field_name} references an unknown source_id"
                )
            if observation.value not in source.text:
                raise IncidentExtractionValidationError(
                    f"{field_name} is absent from its exact source text"
                )

    result = IncidentExtractionResult.model_validate(
        candidate_snapshot.model_dump(mode="python"),
        strict=True,
    )
    if not _json_values_identical(
        candidate_snapshot.to_payload(),
        result.to_payload(),
    ):
        raise IncidentExtractionValidationError(
            "validated result changed candidate JSON type or value identity"
        )
    return result.model_copy(deep=True)


def extract_incident_fields(
    extractor: IncidentFieldExtractor,
    incident_input: IncidentInput,
) -> IncidentExtractionResult:
    """Run one extractor against a copy, then apply the provenance gate."""

    if not isinstance(incident_input, IncidentInput):
        raise TypeError("incident_input must be an IncidentInput")
    validation_input = incident_input.model_copy(deep=True)
    candidate = extractor.extract(incident_input.model_copy(deep=True))
    if not isinstance(candidate, IncidentExtractionCandidate):
        raise TypeError("extractor must return an IncidentExtractionCandidate")
    return validate_incident_extraction(
        validation_input,
        candidate.model_copy(deep=True),
    )


def _json_values_identical(left: Any, right: Any) -> bool:
    """Compare recursive JSON values without Python scalar coercion."""

    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        if left.keys() != right.keys():
            return False
        return all(
            _json_values_identical(left[key], right[key]) for key in left
        )
    if isinstance(left, Sequence) and not isinstance(left, (str, bytes, bytearray)):
        return len(left) == len(right) and all(
            _json_values_identical(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return bool(left == right)
