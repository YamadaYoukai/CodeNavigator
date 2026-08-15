"""Derive exact final-answer citations from successful Tool trace facts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .events import FinalAnswer, RecordedEvent, ToolCall, ToolResult
from .tool_router import GET_FILE_CONTEXT, SEARCH_CODE


_MAX_FILE_CONTEXT_LINES = 201


@dataclass(frozen=True, slots=True)
class FinalAnswerEvidenceValidation:
    """Detached result of validating one model-supplied evidence list."""

    submitted_evidence: tuple[str, ...]
    allowed_evidence: frozenset[str]
    invalid_evidence: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        """Return whether every submitted citation is backed by this trace."""

        return bool(self.submitted_evidence) and not self.invalid_evidence

    def is_allowed(self, citation: str) -> bool:
        """Check one complete citation by exact set membership."""

        return citation in self.allowed_evidence


def collect_allowed_final_answer_evidence(
    events: Iterable[RecordedEvent],
    *,
    task_id: str,
) -> frozenset[str]:
    """Build citations authorized by correlated successful Tool results.

    Only an adjacent ``ToolCall -> ToolResult`` pair from ``task_id`` is
    considered. Search results authorize their exact hit line. File-context
    results authorize each line in their actual returned inclusive range.
    Caller-provided context, model output, failed tools, malformed results, and
    events at or after a terminal event cannot add citations.
    """

    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")

    recorded_events = tuple(events)
    allowed: set[str] = set()

    for index, event in enumerate(recorded_events):
        if isinstance(event, FinalAnswer):
            break
        if not isinstance(event, ToolCall):
            continue
        if index + 1 >= len(recorded_events):
            continue

        result = recorded_events[index + 1]
        if not _is_correlated_success(event, result, task_id=task_id):
            continue

        if event.tool_name == SEARCH_CODE:
            allowed.update(_search_result_citations(result))
        elif event.tool_name == GET_FILE_CONTEXT:
            allowed.update(_file_context_citations(result))

    return frozenset(allowed)


def validate_final_answer_evidence(
    evidence: Iterable[str],
    *,
    events: Iterable[RecordedEvent],
    task_id: str,
) -> FinalAnswerEvidenceValidation:
    """Validate a complete model evidence list without fuzzy matching."""

    submitted = tuple(evidence)
    if not all(isinstance(citation, str) for citation in submitted):
        raise TypeError("evidence must contain only strings")

    allowed = collect_allowed_final_answer_evidence(events, task_id=task_id)
    invalid = tuple(citation for citation in submitted if citation not in allowed)
    return FinalAnswerEvidenceValidation(
        submitted_evidence=submitted,
        allowed_evidence=allowed,
        invalid_evidence=invalid,
    )


def _is_correlated_success(
    call: ToolCall,
    candidate: RecordedEvent,
    *,
    task_id: str,
) -> bool:
    if not isinstance(candidate, ToolResult):
        return False
    if call.task_id != task_id or candidate.task_id != task_id:
        return False
    if call.sequence is None or candidate.sequence != call.sequence + 1:
        return False
    return candidate.call_id == call.call_id and candidate.status == "success"


def _search_result_citations(result: ToolResult) -> set[str]:
    payload = result.result
    if not isinstance(payload, dict):
        return set()
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return set()

    citations: set[str] = set()
    for match in matches:
        if not isinstance(match, dict):
            continue
        citation = _canonical_citation(
            match.get("repo"),
            match.get("path"),
            match.get("line"),
        )
        if citation is not None:
            citations.add(citation)
    return citations


def _file_context_citations(result: ToolResult) -> set[str]:
    payload = result.result
    if not isinstance(payload, dict):
        return set()

    repository = payload.get("repository")
    file_path = payload.get("file_path")
    start_line = payload.get("start_line")
    end_line = payload.get("end_line")
    if not _is_source_component(repository) or not _is_source_component(file_path):
        return set()
    if not _is_positive_line(start_line) or not _is_positive_line(end_line):
        return set()
    if start_line > end_line or end_line - start_line + 1 > _MAX_FILE_CONTEXT_LINES:
        return set()

    return {
        f"{repository}/{file_path}:{line_number}"
        for line_number in range(start_line, end_line + 1)
    }


def _canonical_citation(
    repository: object,
    file_path: object,
    line_number: object,
) -> str | None:
    if not _is_source_component(repository) or not _is_source_component(file_path):
        return None
    if not _is_positive_line(line_number):
        return None
    return f"{repository}/{file_path}:{line_number}"


def _is_source_component(value: object) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if value.startswith("/") or value.endswith("/"):
        return False
    if ":" in value or "\\" in value or "\n" in value or "\r" in value:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _is_positive_line(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
