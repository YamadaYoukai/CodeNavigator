"""Provider-independent single-case Incident analysis orchestration."""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .incident_analysis import (
    DEFAULT_CONTEXT_ARTIFACT_PATH, Identity, Text,
    IncidentAnalysisCandidate, IncidentAnalysisResult, validate_incident_analysis,
)
from .incident_extraction import IncidentInput
from .model_boundary import FinalAnswerCitation


class IncidentCodeLocation(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    citation: FinalAnswerCitation
    snippet: Text


class IncidentAnalyzerInput(BaseModel):
    """Source-only projection; no expectations or evaluation metadata."""
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    case_id: Identity
    incident_input: IncidentInput
    code_locations: tuple[IncidentCodeLocation, ...] = Field(min_length=1)

    def to_payload(self) -> dict:
        return self.model_dump(mode='json')

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)


class IncidentAnalyzer(Protocol):
    def analyze(self, analysis_input: IncidentAnalyzerInput) -> IncidentAnalysisCandidate:
        """Generate one untrusted candidate without owning source validation."""
        ...


def load_incident_analysis_input(
    *, fixture_path: Path | None = None,
    search_artifact_path: Path | None = None,
    context_artifact_path: Path = DEFAULT_CONTEXT_ARTIFACT_PATH,
) -> IncidentAnalyzerInput:
    """Reuse the existing source gate, then expose only verified source text."""
    from evaluation.replay_incident_context import (
        DEFAULT_FIXTURE_PATH, DEFAULT_SEARCH_ARTIFACT_PATH,
        load_context_replay, validate_execution_artifact,
    )

    replay = load_context_replay(
        fixture_path if fixture_path is not None else DEFAULT_FIXTURE_PATH,
        search_artifact_path if search_artifact_path is not None else DEFAULT_SEARCH_ARTIFACT_PATH,
    )
    artifact = validate_execution_artifact(context_artifact_path.read_bytes(), replay)
    if artifact.replay.status != 'success':
        raise ValueError('analysis requires the verified successful context')
    match = artifact.replay.context_match
    return IncidentAnalyzerInput(
        case_id=replay.fixture.persisted.case_id,
        incident_input=replay.fixture.incident_input.model_copy(deep=True),
        code_locations=(IncidentCodeLocation(
            citation=FinalAnswerCitation(
                repo=match.repository, path=match.file_path, line=match.target_line,
            ),
            snippet=replay.fixture.persisted.gold.snippet,
        ),),
    ).model_copy(deep=True)


class FakeIncidentAnalyzer:
    """Ordered untrusted candidates and isolated snapshots of every attempt."""

    def __init__(self, candidates: Iterable[IncidentAnalysisCandidate]) -> None:
        script = tuple(candidates)
        if not all(isinstance(item, IncidentAnalysisCandidate) for item in script):
            raise TypeError('candidates must contain only IncidentAnalysisCandidate instances')
        self._candidates = tuple(item.model_copy(deep=True) for item in script)
        self._analysis_inputs: list[IncidentAnalyzerInput] = []
        self._next_candidate = 0

    @property
    def analysis_inputs(self) -> tuple[IncidentAnalyzerInput, ...]:
        return tuple(item.model_copy(deep=True) for item in self._analysis_inputs)

    @property
    def call_count(self) -> int:
        return len(self._analysis_inputs)

    @property
    def remaining_candidates(self) -> int:
        return len(self._candidates) - self._next_candidate

    def analyze(self, analysis_input: IncidentAnalyzerInput) -> IncidentAnalysisCandidate:
        if not isinstance(analysis_input, IncidentAnalyzerInput):
            raise TypeError('analysis_input must be an IncidentAnalyzerInput')
        self._analysis_inputs.append(analysis_input.model_copy(deep=True))
        if self._next_candidate >= len(self._candidates):
            raise RuntimeError('fake analyzer has no scripted candidates remaining')
        candidate = self._candidates[self._next_candidate]
        self._next_candidate += 1
        return candidate.model_copy(deep=True)


def analyze_incident(
    analyzer: IncidentAnalyzer, *, fixture_path: Path | None = None,
    search_artifact_path: Path | None = None,
    context_artifact_path: Path = DEFAULT_CONTEXT_ARTIFACT_PATH,
) -> IncidentAnalysisResult:
    """Validate sources, invoke once on a copy, then always validate the output.

    Result instances are Candidate subclasses and receive the same validation.
    Exceptions propagate; invalid returns never become successful results.
    Alternate paths are only for copies of the frozen single-case inputs.
    """
    analysis_input = load_incident_analysis_input(
        fixture_path=fixture_path, search_artifact_path=search_artifact_path,
        context_artifact_path=context_artifact_path,
    )
    candidate = analyzer.analyze(analysis_input.model_copy(deep=True))
    if not isinstance(candidate, IncidentAnalysisCandidate):
        raise TypeError('analyzer must return an IncidentAnalysisCandidate')
    return validate_incident_analysis(
        candidate, fixture_path=fixture_path,
        search_artifact_path=search_artifact_path,
        context_artifact_path=context_artifact_path,
    )
