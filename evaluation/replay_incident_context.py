#!/usr/bin/env python3
"""Replay one file-context suffix from a frozen successful search artifact.

This runner does not search again and never calls a model or an Agent Loop. It
strictly validates the public Incident fixture and the immutable 2026-08-30
search artifact, copies the Top-1 location into one ``get_file_context``
decision, and executes that decision through the existing Tool boundary once.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evaluation.replay_incident_search import (
    DEFAULT_FIXTURE_PATH,
    EXPECTED_CASE_ID,
    EXPECTED_GOLD_LINE,
    EXPECTED_GOLD_PATH,
    EXPECTED_GOLD_SNIPPET,
    EXPECTED_GOLD_SNIPPET_SHA256,
    EXPECTED_REPOSITORY,
    FROZEN_FIXTURE_SHA256,
    PINNED_CLICK_REVISION,
    FixtureValidationError,
    IncidentSearchExecutionArtifact,
    ValidatedIncidentSearchFixture,
    _revalidate_execution_fixture,
    load_fixture,
)
from src.examples.code_understanding_agent.context import (
    ContextState,
    Evidence,
    EvidenceKind,
)
from src.examples.code_understanding_agent.events import ToolCall, ToolResult
from src.examples.code_understanding_agent.model_boundary import ToolCallDecision
from src.examples.code_understanding_agent.tool_router import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    GetFileContextArguments,
    PydanticToolAdapter,
    SearchCodeArguments,
    ToolRouter,
)
from src.examples.code_understanding_agent.tool_step import (
    ToolStepExecutor,
    ToolStepOutcome,
)
from src.examples.code_understanding_agent.trace import TraceRecorder


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEARCH_ARTIFACT_PATH = (
    PROJECT_ROOT
    / "evaluation/reports/incident-search-replay-real-2026-08-30.json"
)
FROZEN_SEARCH_ARTIFACT_SHA256 = (
    "41607f1a163440d3b517bb05e637f15dc8c1924b11c26323b9f91f57a5500acf"
)
EXPECTED_SEARCH_TOOL_RESULT_SHA256 = (
    "af3be0e24a5c84950215d62178da6a0f6220e2bbaebc5f85f0339fbe69bb858e"
)
EXPECTED_QUERY_SHA256 = (
    "7866062172bda6b74e1315bb8c9bd77319533c363f314b3407790803be743e60"
)
EXPECTED_CONTEXT_CALL_ID = f"{EXPECTED_CASE_ID}-get-file-context-1"
EXPECTED_LINES_BEFORE = 20
EXPECTED_LINES_AFTER = 20
EXPECTED_CONTEXT_START_LINE = 1264
EXPECTED_CONTEXT_END_LINE = 1304
EXPECTED_TOTAL_LINES = 3542
EXPECTED_CONTEXT_CONTENT_SHA256 = (
    "33955d1d88da4656c038ed3e3b4e5a24201b57477d95bf9ab15cbc466bf728d4"
)
EXPECTED_EVIDENCE_ITEM_BUDGET = 3
EXPECTED_CURRENT_TASK = (
    "Read the frozen Top-1 Click source location exactly once; do not infer a cause."
)

ToolHandler = Callable[..., object]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ContextReplayCallCounts(_StrictModel):
    search_code: Literal[0]
    get_file_context: Literal[1]
    model: Literal[0]
    agent_loop: Literal[0]


class ContextReplayBudget(_StrictModel):
    before: Literal[1]
    after: Literal[0]


class ContextSourceChain(_StrictModel):
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    search_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    search_tool_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContextStateChecks(_StrictModel):
    new_context_fact_in_next_state: bool
    new_context_fact_in_next_model_input: bool
    validated_search_fact_in_next_state: bool
    validated_search_fact_in_next_model_input: bool
    incident_source_fact_in_next_state: bool
    incident_source_fact_in_next_model_input: bool


class ContextMatchReport(_StrictModel):
    repository: str
    file_path: str
    target_line: int = Field(strict=True, ge=1)
    start_line: int = Field(strict=True, ge=1)
    end_line: int = Field(strict=True, ge=1)
    total_lines: int = Field(strict=True, ge=1)
    truncated: bool
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class IncidentContextReplayReport(_StrictModel):
    status: Literal["success", "error"]
    error_type: str | None
    case_id: str
    click_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository: str
    context_arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_chain: ContextSourceChain
    calls: ContextReplayCallCounts
    budget: ContextReplayBudget
    trace_event_types: tuple[str, ...]
    tool_result_status: Literal["success", "error"]
    tool_result_error_type: str | None
    tool_result_sha256: str | None
    context_match: ContextMatchReport | None
    state_checks: ContextStateChecks


class ContextPreflightChecks(_StrictModel):
    repository_root: Literal[True]
    checkout_revision: Literal[True]
    zoekt_name: Literal[True]
    target_file: Literal[True]
    target_line: Literal[True]
    context_bounds: Literal[True]
    context_content: Literal[True]


class IncidentContextPreflightReport(_StrictModel):
    status: Literal["passed"]
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    search_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    click_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository: str
    context_arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checks: ContextPreflightChecks


class IncidentContextExecutionArtifact(_StrictModel):
    preflight: IncidentContextPreflightReport
    replay: IncidentContextReplayReport


@dataclass(frozen=True, slots=True)
class ValidatedIncidentContextReplay:
    fixture: ValidatedIncidentSearchFixture
    search_artifact_sha256: str
    search_artifact_contract_sha256: str
    search_artifact: IncidentSearchExecutionArtifact
    state_contract_sha256: str
    state: ContextState
    search_evidence: Evidence
    decision: ToolCallDecision


@dataclass(frozen=True, slots=True)
class IncidentContextReplayExecution:
    replay_input: ValidatedIncidentContextReplay
    trace: TraceRecorder
    outcome: ToolStepOutcome
    report: IncidentContextReplayReport


class ContextReplayValidationError(ValueError):
    """A stable fail-closed input or saved-artifact error."""

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__(error_type)


class ContextEnvironmentPreflightError(RuntimeError):
    """A stable local preflight error without paths or source content."""

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__(error_type)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _load_search_artifact(
    path: Path,
    fixture: ValidatedIncidentSearchFixture,
) -> tuple[IncidentSearchExecutionArtifact, str, str]:
    try:
        encoded = path.read_bytes()
    except OSError:
        raise ContextReplayValidationError(
            "search_artifact_unavailable"
        ) from None

    digest = _sha256_bytes(encoded)
    if digest != FROZEN_SEARCH_ARTIFACT_SHA256:
        raise ContextReplayValidationError(
            "search_artifact_hash_mismatch"
        ) from None

    try:
        artifact = IncidentSearchExecutionArtifact.model_validate_json(
            encoded,
            strict=True,
        )
        _validate_upstream_contract(fixture, artifact)
    except (ValidationError, TypeError, ValueError):
        raise ContextReplayValidationError(
            "invalid_search_artifact_contract"
        ) from None

    return (
        artifact.model_copy(deep=True),
        digest,
        _canonical_json_sha256(artifact.model_dump(mode="json")),
    )


def _validate_upstream_contract(
    fixture: ValidatedIncidentSearchFixture,
    artifact: IncidentSearchExecutionArtifact,
) -> None:
    if fixture.fixture_sha256 != FROZEN_FIXTURE_SHA256:
        raise ValueError("unexpected fixture digest")

    expected_preflight = {
        "status": "passed",
        "fixture_sha256": fixture.fixture_sha256,
        "click_revision": PINNED_CLICK_REVISION,
        "repository": EXPECTED_REPOSITORY,
        "checks": {
            "zoekt_url_configured": True,
            "checkout_revision": True,
            "declared_index_revision": True,
            "zoekt_name": True,
            "gold_source_line": True,
        },
    }
    if artifact.preflight.model_dump(mode="json") != expected_preflight:
        raise ValueError("unexpected search preflight")

    expected_replay = {
        "status": "success",
        "error_type": None,
        "case_id": EXPECTED_CASE_ID,
        "fixture_sha256": fixture.fixture_sha256,
        "click_revision": PINNED_CLICK_REVISION,
        "repository": EXPECTED_REPOSITORY,
        "query_sha256": EXPECTED_QUERY_SHA256,
        "calls": {
            "search_code": 1,
            "get_file_context": 0,
            "model": 0,
            "agent_loop": 0,
        },
        "budget": {"before": 1, "after": 0},
        "trace_event_types": ["tool_call", "tool_result"],
        "tool_result_status": "success",
        "tool_result_error_type": None,
        "tool_result_sha256": EXPECTED_SEARCH_TOOL_RESULT_SHA256,
        "gold_match": {
            "rank": 1,
            "repo": EXPECTED_REPOSITORY,
            "path": EXPECTED_GOLD_PATH,
            "line": EXPECTED_GOLD_LINE,
            "snippet_sha256": EXPECTED_GOLD_SNIPPET_SHA256,
        },
        "state_checks": {
            "new_tool_fact_in_next_state": True,
            "new_tool_fact_in_next_model_input": True,
            "incident_source_fact_preserved": True,
        },
    }
    if artifact.replay.model_dump(mode="json") != expected_replay:
        raise ValueError("unexpected search replay")
    if _sha256_text(fixture.task.query) != EXPECTED_QUERY_SHA256:
        raise ValueError("unexpected query digest")


def _derive_context_contract(
    fixture: ValidatedIncidentSearchFixture,
    artifact: IncidentSearchExecutionArtifact,
    search_artifact_sha256: str,
) -> tuple[ContextState, Evidence, ToolCallDecision]:
    gold = artifact.replay.gold_match
    if gold is None:
        raise ValueError("missing Top-1 gold")

    arguments = GetFileContextArguments(
        repository=gold.repo,
        file_path=gold.path,
        line_number=gold.line,
        lines_before=EXPECTED_LINES_BEFORE,
        lines_after=EXPECTED_LINES_AFTER,
    )
    decision = ToolCallDecision(
        call_id=EXPECTED_CONTEXT_CALL_ID,
        tool_name=GET_FILE_CONTEXT,
        arguments=arguments.model_dump(mode="json"),
    )
    search_evidence = Evidence(
        kind=EvidenceKind.FACT,
        source=f"artifact:{SEARCH_CODE}:{search_artifact_sha256}",
        content=json.dumps(
            {
                "artifact_sha256": search_artifact_sha256,
                "gold": {
                    "line": gold.line,
                    "path": gold.path,
                    "rank": gold.rank,
                    "repo": gold.repo,
                    "snippet_sha256": gold.snippet_sha256,
                },
                "tool_result_sha256": artifact.replay.tool_result_sha256,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
    state = fixture.state.model_copy(
        update={
            "current_task": EXPECTED_CURRENT_TASK,
            "evidence": (
                *(item.model_copy(deep=True) for item in fixture.state.evidence),
                search_evidence.model_copy(deep=True),
            ),
            "evidence_item_budget": EXPECTED_EVIDENCE_ITEM_BUDGET,
            "remaining_tool_calls": 1,
        },
        deep=True,
    )
    return state, search_evidence, decision


def load_context_replay(
    fixture_path: Path = DEFAULT_FIXTURE_PATH,
    search_artifact_path: Path = DEFAULT_SEARCH_ARTIFACT_PATH,
) -> ValidatedIncidentContextReplay:
    """Strictly link the frozen fixture and successful Top-1 artifact."""

    try:
        fixture = load_fixture(fixture_path)
    except FixtureValidationError as exc:
        raise ContextReplayValidationError(exc.error_type) from None

    artifact, artifact_sha256, artifact_contract_sha256 = (
        _load_search_artifact(search_artifact_path, fixture)
    )
    try:
        state, search_evidence, decision = _derive_context_contract(
            fixture,
            artifact,
            artifact_sha256,
        )
    except (ValidationError, TypeError, ValueError):
        raise ContextReplayValidationError(
            "invalid_context_derivation"
        ) from None

    return ValidatedIncidentContextReplay(
        fixture=fixture,
        search_artifact_sha256=artifact_sha256,
        search_artifact_contract_sha256=artifact_contract_sha256,
        search_artifact=artifact.model_copy(deep=True),
        state_contract_sha256=_canonical_json_sha256(
            state.model_dump(mode="json")
        ),
        state=state.model_copy(deep=True),
        search_evidence=search_evidence.model_copy(deep=True),
        decision=decision.model_copy(deep=True),
    )


def _revalidate_context_input(
    replay_input: ValidatedIncidentContextReplay,
) -> None:
    try:
        if not isinstance(replay_input, ValidatedIncidentContextReplay):
            raise TypeError("unexpected replay input")
        _revalidate_execution_fixture(replay_input.fixture)
        if replay_input.search_artifact_sha256 != FROZEN_SEARCH_ARTIFACT_SHA256:
            raise ValueError("search artifact digest changed")
        artifact_contract_sha256 = _canonical_json_sha256(
            replay_input.search_artifact.model_dump(mode="json")
        )
        if artifact_contract_sha256 != replay_input.search_artifact_contract_sha256:
            raise ValueError("search artifact contract changed")
        _validate_upstream_contract(
            replay_input.fixture,
            replay_input.search_artifact,
        )
        expected_state, expected_evidence, expected_decision = (
            _derive_context_contract(
                replay_input.fixture,
                replay_input.search_artifact,
                replay_input.search_artifact_sha256,
            )
        )
        if (
            replay_input.state != expected_state
            or replay_input.search_evidence != expected_evidence
            or replay_input.decision != expected_decision
            or _canonical_json_sha256(
                replay_input.state.model_dump(mode="json")
            )
            != replay_input.state_contract_sha256
        ):
            raise ValueError("derived context contract changed")
    except (
        ContextReplayValidationError,
        FixtureValidationError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        raise ContextReplayValidationError(
            "context_input_mutated_before_execution"
        ) from None


async def _await_if_needed(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


async def execute_replay(
    replay_input: ValidatedIncidentContextReplay,
    *,
    get_file_context: ToolHandler,
    search_code: ToolHandler | None = None,
) -> IncidentContextReplayExecution:
    """Execute one context suffix; every success or failure stops the run."""

    _revalidate_context_input(replay_input)
    if search_code is not None and not callable(search_code):
        raise TypeError("search_code must be callable when supplied")
    call_counts = {SEARCH_CODE: 0, GET_FILE_CONTEXT: 0}

    async def fail_if_called_search_code(**_: object) -> object:
        call_counts[SEARCH_CODE] += 1
        # Never invoke a supplied search callable: a runner bug must not repeat
        # the already frozen real search.
        raise AssertionError("search_code is forbidden in context replay")

    async def counted_get_file_context(**arguments: object) -> object:
        call_counts[GET_FILE_CONTEXT] += 1
        return await _await_if_needed(get_file_context(**arguments))

    trace = TraceRecorder(task_id=f"{EXPECTED_CASE_ID}-context-suffix")
    router = ToolRouter(
        trace=trace,
        tools={
            SEARCH_CODE: PydanticToolAdapter(
                SearchCodeArguments,
                fail_if_called_search_code,
            ),
            GET_FILE_CONTEXT: PydanticToolAdapter(
                GetFileContextArguments,
                counted_get_file_context,
            ),
        },
    )
    outcome = await ToolStepExecutor(router=router).execute(
        replay_input.state.model_copy(deep=True),
        replay_input.decision.model_copy(deep=True),
    )
    report = _build_report(
        replay_input,
        trace,
        outcome,
        search_code_calls=call_counts[SEARCH_CODE],
        get_file_context_calls=call_counts[GET_FILE_CONTEXT],
    )
    return IncidentContextReplayExecution(
        replay_input=replay_input,
        trace=trace,
        outcome=outcome,
        report=report,
    )


def _build_report(
    replay_input: ValidatedIncidentContextReplay,
    trace: TraceRecorder,
    outcome: ToolStepOutcome,
    *,
    search_code_calls: int,
    get_file_context_calls: int,
) -> IncidentContextReplayReport:
    events = trace.events
    event_types = tuple(event.event_type for event in events)
    result = outcome.tool_result
    expected_arguments = replay_input.decision.arguments
    if (
        len(events) != 2
        or not isinstance(events[0], ToolCall)
        or not isinstance(events[1], ToolResult)
        or event_types != ("tool_call", "tool_result")
        or result is None
        or events[1] != result
        or events[0].tool_name != GET_FILE_CONTEXT
        or events[0].call_id != EXPECTED_CONTEXT_CALL_ID
        or events[0].arguments != expected_arguments
    ):
        raise RuntimeError("context replay trace invariant failed")
    if search_code_calls != 0 or get_file_context_calls != 1:
        raise RuntimeError("context replay invocation invariant failed")
    if (
        replay_input.state.remaining_tool_calls != 1
        or outcome.next_state.remaining_tool_calls != 0
        or outcome.next_model_input.remaining_tool_calls != 0
    ):
        raise RuntimeError("context replay budget invariant failed")

    context_evidence_source = (
        f"tool_result:{GET_FILE_CONTEXT}:{EXPECTED_CONTEXT_CALL_ID}"
    )
    next_state_sources = {item.source for item in outcome.next_state.evidence}
    next_input_sources = {
        item.source for item in outcome.next_model_input.evidence
    }
    incident_sources = {
        item.source for item in replay_input.fixture.state.evidence
    }
    state_checks = ContextStateChecks(
        new_context_fact_in_next_state=(
            context_evidence_source in next_state_sources
        ),
        new_context_fact_in_next_model_input=(
            context_evidence_source in next_input_sources
        ),
        validated_search_fact_in_next_state=(
            replay_input.search_evidence.source in next_state_sources
        ),
        validated_search_fact_in_next_model_input=(
            replay_input.search_evidence.source in next_input_sources
        ),
        incident_source_fact_in_next_state=incident_sources.issubset(
            next_state_sources
        ),
        incident_source_fact_in_next_model_input=incident_sources.issubset(
            next_input_sources
        ),
    )
    if not all(state_checks.model_dump(mode="python").values()):
        raise RuntimeError("context replay state invariant failed")

    error_type: str | None
    result_sha256: str | None = None
    context_match: ContextMatchReport | None = None
    if result.status == "error":
        error_type = result.error_type or "tool_execution_error"
    else:
        result_sha256 = _canonical_json_sha256(result.result)
        error_type, context_match = _inspect_context_result(result.result)

    return IncidentContextReplayReport(
        status="success" if error_type is None else "error",
        error_type=error_type,
        case_id=EXPECTED_CASE_ID,
        click_revision=PINNED_CLICK_REVISION,
        repository=EXPECTED_REPOSITORY,
        context_arguments_sha256=_canonical_json_sha256(expected_arguments),
        source_chain=ContextSourceChain(
            fixture_sha256=replay_input.fixture.fixture_sha256,
            search_artifact_sha256=replay_input.search_artifact_sha256,
            search_tool_result_sha256=EXPECTED_SEARCH_TOOL_RESULT_SHA256,
        ),
        calls=ContextReplayCallCounts(
            search_code=0,
            get_file_context=1,
            model=0,
            agent_loop=0,
        ),
        budget=ContextReplayBudget(before=1, after=0),
        trace_event_types=event_types,
        tool_result_status=result.status,
        tool_result_error_type=result.error_type,
        tool_result_sha256=result_sha256,
        context_match=context_match,
        state_checks=state_checks,
    )


def _inspect_context_result(
    result: object,
) -> tuple[str | None, ContextMatchReport | None]:
    required_keys = {
        "repository",
        "file_path",
        "target_line",
        "start_line",
        "end_line",
        "total_lines",
        "content",
        "truncated",
    }
    if not isinstance(result, Mapping) or set(result) != required_keys:
        return "unexpected_result", None

    string_fields = ("repository", "file_path", "content")
    integer_fields = ("target_line", "start_line", "end_line", "total_lines")
    if (
        any(type(result[field]) is not str for field in string_fields)
        or any(type(result[field]) is not int for field in integer_fields)
        or type(result["truncated"]) is not bool
    ):
        return "unexpected_result", None

    content = result["content"]
    assert isinstance(content, str)
    expected_values = {
        "repository": EXPECTED_REPOSITORY,
        "file_path": EXPECTED_GOLD_PATH,
        "target_line": EXPECTED_GOLD_LINE,
        "start_line": EXPECTED_CONTEXT_START_LINE,
        "end_line": EXPECTED_CONTEXT_END_LINE,
        "total_lines": EXPECTED_TOTAL_LINES,
        "truncated": True,
    }
    if any(result[key] != value for key, value in expected_values.items()):
        return "context_mismatch", None
    if _sha256_text(content) != EXPECTED_CONTEXT_CONTENT_SHA256:
        return "context_mismatch", None

    context_lines = content.splitlines()
    if len(context_lines) != EXPECTED_CONTEXT_END_LINE - EXPECTED_CONTEXT_START_LINE + 1:
        return "context_mismatch", None
    for line_number, context_line in zip(
        range(EXPECTED_CONTEXT_START_LINE, EXPECTED_CONTEXT_END_LINE + 1),
        context_lines,
        strict=True,
    ):
        marker = ">" if line_number == EXPECTED_GOLD_LINE else " "
        if not context_line.startswith(f"{marker}{line_number:5d} | "):
            return "context_mismatch", None
    target_line = context_lines[EXPECTED_GOLD_LINE - EXPECTED_CONTEXT_START_LINE]
    if target_line.split(" | ", maxsplit=1)[1].strip() != EXPECTED_GOLD_SNIPPET:
        return "context_mismatch", None

    return None, ContextMatchReport(
        repository=EXPECTED_REPOSITORY,
        file_path=EXPECTED_GOLD_PATH,
        target_line=EXPECTED_GOLD_LINE,
        start_line=EXPECTED_CONTEXT_START_LINE,
        end_line=EXPECTED_CONTEXT_END_LINE,
        total_lines=EXPECTED_TOTAL_LINES,
        truncated=True,
        content_sha256=EXPECTED_CONTEXT_CONTENT_SHA256,
    )


def _git_output(repository: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def _format_context(source_lines: list[str]) -> str:
    context_lines: list[str] = []
    for line_number in range(
        EXPECTED_CONTEXT_START_LINE,
        EXPECTED_CONTEXT_END_LINE + 1,
    ):
        marker = ">" if line_number == EXPECTED_GOLD_LINE else " "
        context_lines.append(
            f"{marker}{line_number:5d} | {source_lines[line_number - 1]}"
        )
    return "\n".join(context_lines)


def preflight_environment(
    replay_input: ValidatedIncidentContextReplay,
    *,
    repository_root: Path,
) -> IncidentContextPreflightReport:
    """Validate only local source identity; never call a Tool or network."""

    _revalidate_context_input(replay_input)
    root = repository_root.expanduser().resolve()
    if not root.is_dir():
        raise ContextEnvironmentPreflightError("repository_root_unavailable")
    repository = (root / EXPECTED_REPOSITORY).resolve()
    if not repository.is_relative_to(root) or not repository.is_dir():
        raise ContextEnvironmentPreflightError("repository_root_unavailable")

    if _git_output(repository, "rev-parse", "HEAD") != PINNED_CLICK_REVISION:
        raise ContextEnvironmentPreflightError("checkout_revision_mismatch")
    if (
        _git_output(repository, "config", "--get", "zoekt.name")
        != EXPECTED_REPOSITORY
    ):
        raise ContextEnvironmentPreflightError(
            "indexed_repository_name_mismatch"
        )

    target_file = (repository / EXPECTED_GOLD_PATH).resolve()
    if not target_file.is_relative_to(repository) or not target_file.is_file():
        raise ContextEnvironmentPreflightError("target_file_unavailable")
    try:
        source_lines = target_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ContextEnvironmentPreflightError("target_file_unavailable") from None
    if len(source_lines) != EXPECTED_TOTAL_LINES:
        raise ContextEnvironmentPreflightError("context_bounds_mismatch")

    target_index = EXPECTED_GOLD_LINE - 1
    if (
        target_index >= len(source_lines)
        or source_lines[target_index].strip() != EXPECTED_GOLD_SNIPPET
        or _sha256_text(source_lines[target_index].strip())
        != EXPECTED_GOLD_SNIPPET_SHA256
    ):
        raise ContextEnvironmentPreflightError("target_line_mismatch")

    expected_start = max(1, EXPECTED_GOLD_LINE - EXPECTED_LINES_BEFORE)
    expected_end = min(
        len(source_lines),
        EXPECTED_GOLD_LINE + EXPECTED_LINES_AFTER,
    )
    if (
        expected_start != EXPECTED_CONTEXT_START_LINE
        or expected_end != EXPECTED_CONTEXT_END_LINE
    ):
        raise ContextEnvironmentPreflightError("context_bounds_mismatch")
    if _sha256_text(_format_context(source_lines)) != EXPECTED_CONTEXT_CONTENT_SHA256:
        raise ContextEnvironmentPreflightError("context_content_mismatch")

    return IncidentContextPreflightReport(
        status="passed",
        fixture_sha256=replay_input.fixture.fixture_sha256,
        search_artifact_sha256=replay_input.search_artifact_sha256,
        click_revision=PINNED_CLICK_REVISION,
        repository=EXPECTED_REPOSITORY,
        context_arguments_sha256=_canonical_json_sha256(
            replay_input.decision.arguments
        ),
        checks=ContextPreflightChecks(
            repository_root=True,
            checkout_revision=True,
            zoekt_name=True,
            target_file=True,
            target_line=True,
            context_bounds=True,
            context_content=True,
        ),
    )


def validate_execution_artifact(
    encoded: bytes,
    replay_input: ValidatedIncidentContextReplay,
) -> IncidentContextExecutionArtifact:
    """Strictly parse and recompute invariants for one saved artifact."""

    _revalidate_context_input(replay_input)
    try:
        artifact = IncidentContextExecutionArtifact.model_validate_json(
            encoded,
            strict=True,
        )
    except (ValidationError, ValueError):
        raise ContextReplayValidationError(
            "invalid_context_execution_artifact"
        ) from None

    expected_arguments_sha256 = _canonical_json_sha256(
        replay_input.decision.arguments
    )
    expected_chain = ContextSourceChain(
        fixture_sha256=replay_input.fixture.fixture_sha256,
        search_artifact_sha256=replay_input.search_artifact_sha256,
        search_tool_result_sha256=EXPECTED_SEARCH_TOOL_RESULT_SHA256,
    )
    valid_preflight = (
        artifact.preflight.status == "passed"
        and artifact.preflight.fixture_sha256
        == replay_input.fixture.fixture_sha256
        and artifact.preflight.search_artifact_sha256
        == replay_input.search_artifact_sha256
        and artifact.preflight.click_revision == PINNED_CLICK_REVISION
        and artifact.preflight.repository == EXPECTED_REPOSITORY
        and artifact.preflight.context_arguments_sha256
        == expected_arguments_sha256
        and all(artifact.preflight.checks.model_dump(mode="python").values())
    )
    report = artifact.replay
    valid_replay = (
        report.case_id == EXPECTED_CASE_ID
        and report.click_revision == PINNED_CLICK_REVISION
        and report.repository == EXPECTED_REPOSITORY
        and report.context_arguments_sha256 == expected_arguments_sha256
        and report.source_chain == expected_chain
        and report.calls
        == ContextReplayCallCounts(
            search_code=0,
            get_file_context=1,
            model=0,
            agent_loop=0,
        )
        and report.budget == ContextReplayBudget(before=1, after=0)
        and report.trace_event_types == ("tool_call", "tool_result")
        and all(report.state_checks.model_dump(mode="python").values())
    )
    if report.status == "success":
        valid_outcome = (
            report.error_type is None
            and report.tool_result_status == "success"
            and report.tool_result_error_type is None
            and report.tool_result_sha256 is not None
            and report.context_match
            == ContextMatchReport(
                repository=EXPECTED_REPOSITORY,
                file_path=EXPECTED_GOLD_PATH,
                target_line=EXPECTED_GOLD_LINE,
                start_line=EXPECTED_CONTEXT_START_LINE,
                end_line=EXPECTED_CONTEXT_END_LINE,
                total_lines=EXPECTED_TOTAL_LINES,
                truncated=True,
                content_sha256=EXPECTED_CONTEXT_CONTENT_SHA256,
            )
        )
    else:
        valid_outcome = (
            report.error_type is not None
            and report.context_match is None
            and (
                (
                    report.tool_result_status == "error"
                    and report.tool_result_error_type == report.error_type
                    and report.tool_result_sha256 is None
                )
                or (
                    report.tool_result_status == "success"
                    and report.tool_result_error_type is None
                    and report.tool_result_sha256 is not None
                )
            )
        )
    if not (valid_preflight and valid_replay and valid_outcome):
        raise ContextReplayValidationError(
            "invalid_context_execution_artifact"
        )
    return artifact.model_copy(deep=True)


def _required_repository_root() -> Path:
    value = os.getenv("REPOSITORY_ROOT")
    if value is None or not value.strip():
        raise ContextEnvironmentPreflightError("repository_root_missing")
    return Path(value).expanduser().resolve()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or execute one get_file_context suffix from the frozen "
            "successful Incident search artifact."
        )
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument(
        "--search-artifact",
        type=Path,
        default=DEFAULT_SEARCH_ARTIFACT_PATH,
    )
    parser.add_argument("--out", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--preflight-only", action="store_true")
    return parser.parse_args(argv)


def _print_json(value: object, *, file: Any = sys.stdout) -> None:
    print(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        file=file,
    )


def _print_error(error_type: str) -> None:
    _print_json({"status": "error", "error_type": error_type}, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        replay_input = load_context_replay(
            fixture_path=args.fixture,
            search_artifact_path=args.search_artifact,
        )
    except ContextReplayValidationError as exc:
        _print_error(exc.error_type)
        return 2

    if args.validate_only:
        _print_json(
            {
                "status": "valid",
                "case_id": EXPECTED_CASE_ID,
                "fixture_sha256": replay_input.fixture.fixture_sha256,
                "search_artifact_sha256": (
                    replay_input.search_artifact_sha256
                ),
                "context_arguments_sha256": _canonical_json_sha256(
                    replay_input.decision.arguments
                ),
                "search_code_calls": 0,
                "get_file_context_calls": 0,
                "model_calls": 0,
                "agent_loop_calls": 0,
            }
        )
        return 0

    if not args.preflight_only and args.out is None:
        _print_error("output_path_required")
        return 2
    if args.out is not None and args.out.exists():
        _print_error("output_already_exists")
        return 2

    try:
        preflight = preflight_environment(
            replay_input,
            repository_root=_required_repository_root(),
        )
    except ContextEnvironmentPreflightError as exc:
        _print_error(exc.error_type)
        return 2

    if args.preflight_only:
        _print_json(preflight.model_dump(mode="json"))
        return 0

    try:
        from src.server import get_file_context

        execution = asyncio.run(
            execute_replay(
                replay_input,
                get_file_context=get_file_context,
            )
        )
        artifact = IncidentContextExecutionArtifact(
            preflight=preflight,
            replay=execution.report,
        )
        encoded = (
            json.dumps(
                artifact.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        validate_execution_artifact(encoded, replay_input)
        assert args.out is not None
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("xb") as output_file:
            output_file.write(encoded)
    except FileExistsError:
        _print_error("output_already_exists")
        return 4
    except Exception:
        _print_error("context_replay_failed")
        return 4

    _print_json(artifact.model_dump(mode="json"))
    return 0 if execution.report.status == "success" else 3


if __name__ == "__main__":
    raise SystemExit(main())
