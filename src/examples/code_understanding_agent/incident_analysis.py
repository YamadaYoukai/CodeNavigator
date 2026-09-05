"""Offline four-section output gate for the single frozen public Click case.

No inference, execution or causal-quality assessment happens at this boundary.
The evaluation imports are local because the pinned replay is a fixture-specific
source authority, not a runtime dependency of the generic agent loop.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .incident_extraction import (
    IncidentExtractionCandidate, SourcedIncidentValue, validate_incident_extraction,
)
from .model_boundary import FinalAnswerCitation

DEFAULT_CONTEXT_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[3]
    / 'evaluation/reports/incident-context-replay-real-2026-09-02.json'
)
Text = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r'\S')]
Identity = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r'^\S+$')]


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)

    def to_payload(self) -> dict:
        return self.model_dump(mode='json')

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False)


class IncidentFact(_Model):
    kind: Literal['incident']
    fact_id: Identity
    observation: SourcedIncidentValue


class CodeFact(_Model):
    kind: Literal['code']
    fact_id: Identity
    citation: FinalAnswerCitation
    snippet: Text


class CauseHypothesis(_Model):
    hypothesis_id: Identity
    statement: Text
    supporting_fact_ids: tuple[Identity, ...] = Field(min_length=1)
    confidence: Literal['low', 'medium', 'high']


class MissingInformation(_Model):
    information_id: Identity
    question: Text


class InvestigationStep(_Model):
    step_id: Identity
    action: Text
    related_hypothesis_ids: tuple[Identity, ...]


def _unique(ids: tuple[str, ...]) -> set[str]:
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate identity or reference')
    return set(ids)


class IncidentAnalysisCandidate(_Model):
    """Untrusted structure; only validate_incident_analysis establishes sources."""
    case_id: Identity
    facts: tuple[Annotated[IncidentFact | CodeFact, Field(discriminator='kind')], ...]
    hypotheses: tuple[CauseHypothesis, ...]
    missing_information: tuple[MissingInformation, ...]
    investigation_steps: tuple[InvestigationStep, ...]

    @model_validator(mode='after')
    def validate_links(self) -> IncidentAnalysisCandidate:
        facts = _unique(tuple(f.fact_id for f in self.facts))
        hypotheses = _unique(tuple(h.hypothesis_id for h in self.hypotheses))
        _unique(tuple(m.information_id for m in self.missing_information))
        _unique(tuple(s.step_id for s in self.investigation_steps))
        if not self.hypotheses and not self.missing_information:
            raise ValueError('no hypotheses requires missing information')
        for hypothesis in self.hypotheses:
            if not _unique(hypothesis.supporting_fact_ids) <= facts:
                raise ValueError('unknown supporting fact')
        for step in self.investigation_steps:
            if not _unique(step.related_hypothesis_ids) <= hypotheses:
                raise ValueError('unknown related hypothesis')
        return self


class IncidentAnalysisResult(IncidentAnalysisCandidate):
    """Detached validated snapshot; class identity alone is not a trust proof."""


def validate_incident_analysis(
    candidate: IncidentAnalysisCandidate | str | bytes | dict,
    *,
    fixture_path: Path | None = None,
    search_artifact_path: Path | None = None,
    context_artifact_path: Path = DEFAULT_CONTEXT_ARTIFACT_PATH,
) -> IncidentAnalysisResult:
    """Reload the pinned sources and strictly revalidate an untrusted candidate.

    JSON strings/bytes accept arrays; Python mappings require strict tuples.
    Hypothesis entailment, confidence calibration and action safety are outside
    this contract. Alternate paths are only for copies of the frozen inputs.
    """
    from evaluation.replay_incident_context import (
        DEFAULT_FIXTURE_PATH, DEFAULT_SEARCH_ARTIFACT_PATH,
        load_context_replay, validate_execution_artifact,
    )

    if isinstance(candidate, IncidentAnalysisCandidate):
        raw = candidate.model_dump(mode='python', warnings=False)
        snapshot = IncidentAnalysisCandidate.model_validate(deepcopy(raw), strict=True)
    elif isinstance(candidate, (str, bytes)):
        snapshot = IncidentAnalysisCandidate.model_validate_json(candidate, strict=True)
    elif isinstance(candidate, dict):
        snapshot = IncidentAnalysisCandidate.model_validate(deepcopy(candidate), strict=True)
    else:
        raise ValueError('expected a structured Incident analysis candidate')

    replay = load_context_replay(
        fixture_path if fixture_path is not None else DEFAULT_FIXTURE_PATH,
        search_artifact_path if search_artifact_path is not None else DEFAULT_SEARCH_ARTIFACT_PATH,
    )
    artifact = validate_execution_artifact(context_artifact_path.read_bytes(), replay)
    if artifact.replay.status != 'success':
        raise ValueError('analysis requires the verified successful context')
    fixture = replay.fixture
    if snapshot.case_id != fixture.persisted.case_id:
        raise ValueError('analysis belongs to a different case')
    gold = fixture.persisted.gold
    for fact in snapshot.facts:
        if isinstance(fact, IncidentFact):
            # The extraction field is only a carrier for the existing generic
            # exact-excerpt/source-ID gate, not a new extraction operation.
            validate_incident_extraction(
                fixture.incident_input.model_copy(deep=True),
                IncidentExtractionCandidate(
                    exception_class=None, method=None, error_text=fact.observation,
                    service_name=None, configuration_keys=(),
                ),
            )
        else:
            if (fact.citation.repo != gold.repo or fact.citation.path != gold.path
                    or fact.citation.line != gold.line or fact.snippet != gold.snippet):
                raise ValueError('code observation differs from verified single-line gold')
    return IncidentAnalysisResult.model_validate(
        snapshot.model_dump(mode='python'), strict=True,
    ).model_copy(deep=True)
