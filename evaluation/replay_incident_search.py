#!/usr/bin/env python3
"""Replay one frozen, source-grounded Incident retrieval task exactly once.

The fixture is public and deterministic.  This module validates its recorded
digest, repeats the Incident provenance gate, rebuilds the typed retrieval
task, and sends one caller-supplied ``search_code`` decision through the
existing ``ToolStepExecutor -> ToolRouter -> PydanticToolAdapter`` boundary.
It never calls a model, enters an agent loop, retries, or reads file context.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.examples.code_understanding_agent.context import (
    ContextState,
    Evidence,
    RepositoryHint,
)
from src.examples.code_understanding_agent.events import ToolCall, ToolResult
from src.examples.code_understanding_agent.incident_context import (
    IncidentContextValidationError,
    IncidentRetrievalTask,
    map_incident_to_retrieval_context,
)
from src.examples.code_understanding_agent.incident_extraction import (
    IncidentExtractionCandidate,
    IncidentExtractionResult,
    IncidentExtractionValidationError,
    IncidentInput,
    validate_incident_extraction,
)
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
DEFAULT_FIXTURE_PATH = (
    PROJECT_ROOT / "evaluation/fixtures/incident-click-search-public.json"
)
PINNED_CLICK_REVISION = "6eeb50e948ea136db145280f6f5dd52eca3fa7e5"
FROZEN_FIXTURE_SHA256 = (
    "2634a72fca1c8702e0250b8948241ae0d0a6dceaf18e8f6b56039a5734944fe0"
)
EXPECTED_CASE_ID = "incident-click-unexpected-extra-argument-001"
EXPECTED_QUERY = "Got unexpected extra argument"
EXPECTED_REPOSITORY = "click"
EXPECTED_CALL_ID = f"{EXPECTED_CASE_ID}-search-code-1"
EXPECTED_GOLD_PATH = "src/click/core.py"
EXPECTED_GOLD_LINE = 1284
EXPECTED_GOLD_SNIPPET = '"Got unexpected extra argument ({args})",'
EXPECTED_GOLD_SNIPPET_SHA256 = (
    "6f4394e966077bc34c53dc38e6b98823f478a8e6cb31195359cb43f70e0ef2c2"
)

ToolHandler = Callable[..., object]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _PersistedIncidentSource(_StrictModel):
    source_id: str = Field(min_length=1)
    source_type: Literal["description", "log", "stack_trace"]
    text: str = Field(min_length=1)


class _PersistedIncidentInput(_StrictModel):
    sources: list[_PersistedIncidentSource] = Field(min_length=1)


class _PersistedSourcedValue(_StrictModel):
    value: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


class _PersistedExtractionCandidate(_StrictModel):
    exception_class: _PersistedSourcedValue | None
    method: _PersistedSourcedValue | None
    error_text: _PersistedSourcedValue | None
    service_name: _PersistedSourcedValue | None
    configuration_keys: list[_PersistedSourcedValue]


class _PersistedRepositoryHint(_StrictModel):
    canonical_name: str = Field(min_length=1)
    aliases: list[str]


class _PersistedTaskRule(_StrictModel):
    task_count: Literal[1]
    field_name: Literal["error_text"]
    source_ids: list[str] = Field(min_length=1)
    call_id: str = Field(min_length=1)


class _PersistedSearchArguments(_StrictModel):
    query: str = Field(min_length=1)
    repo: str
    lang: None
    path: None
    limit: Literal[20]
    literal: Literal[True]


class _PersistedContextPolicy(_StrictModel):
    system_instruction: str = Field(min_length=1)
    evidence_item_budget: Literal[2]
    remaining_tool_calls: Literal[1]


class _PersistedGold(_StrictModel):
    repo: str
    path: str
    line: int = Field(ge=1)
    snippet: str = Field(min_length=1)
    snippet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class IncidentSearchReplayFixture(_StrictModel):
    """Every public input needed to rebuild one Incident retrieval decision."""

    schema_version: Literal[1]
    case_id: str = Field(min_length=1)
    click_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    incident_input: _PersistedIncidentInput
    extraction_candidate: _PersistedExtractionCandidate
    repository_hint: _PersistedRepositoryHint
    selected_repository: str = Field(min_length=1)
    task_rule: _PersistedTaskRule
    search_arguments: _PersistedSearchArguments
    context_policy: _PersistedContextPolicy
    gold: _PersistedGold


class CallCountReport(_StrictModel):
    search_code: int = Field(ge=0)
    get_file_context: int = Field(ge=0)
    model: Literal[0]
    agent_loop: Literal[0]


class BudgetReport(_StrictModel):
    before: int = Field(ge=0)
    after: int = Field(ge=0)


class GoldMatchReport(_StrictModel):
    rank: int = Field(ge=1)
    repo: str
    path: str
    line: int = Field(ge=1)
    snippet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StateChecksReport(_StrictModel):
    new_tool_fact_in_next_state: bool
    new_tool_fact_in_next_model_input: bool
    incident_source_fact_preserved: bool


class IncidentSearchReplayReport(_StrictModel):
    """Allowlisted public outcome; raw inputs and backend details are excluded."""

    status: Literal["success", "error"]
    error_type: str | None
    case_id: str
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    click_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository: str
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    calls: CallCountReport
    budget: BudgetReport
    trace_event_types: tuple[str, ...]
    tool_result_status: Literal["success", "error"]
    tool_result_error_type: str | None
    tool_result_sha256: str | None
    gold_match: GoldMatchReport | None
    state_checks: StateChecksReport


class PreflightChecksReport(_StrictModel):
    zoekt_url_configured: bool
    checkout_revision: bool
    declared_index_revision: bool
    zoekt_name: bool
    gold_source_line: bool


class IncidentSearchPreflightReport(_StrictModel):
    status: Literal["passed"]
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    click_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository: str
    checks: PreflightChecksReport


class IncidentSearchExecutionArtifact(_StrictModel):
    preflight: IncidentSearchPreflightReport
    replay: IncidentSearchReplayReport


@dataclass(frozen=True, slots=True)
class ValidatedIncidentSearchFixture:
    fixture_sha256: str
    contract_sha256: str
    persisted: IncidentSearchReplayFixture
    incident_input: IncidentInput
    extraction_result: IncidentExtractionResult
    tasks: tuple[IncidentRetrievalTask, ...]
    task: IncidentRetrievalTask
    state: ContextState
    decision: ToolCallDecision


@dataclass(frozen=True, slots=True)
class IncidentSearchReplayExecution:
    fixture: ValidatedIncidentSearchFixture
    trace: TraceRecorder
    outcome: ToolStepOutcome
    report: IncidentSearchReplayReport


class FixtureValidationError(ValueError):
    """A stable fail-closed fixture error without source content."""

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__(error_type)


class EnvironmentPreflightError(RuntimeError):
    """A stable local preflight failure that never includes configuration."""

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


def _load_and_verify_fixture_bytes(
    path: Path,
    checksum_path: Path | None,
) -> tuple[bytes, str]:
    try:
        encoded = path.read_bytes()
        digest_line = (checksum_path or path.with_suffix(path.suffix + ".sha256"))
        recorded = digest_line.read_text(encoding="utf-8")
    except OSError:
        raise FixtureValidationError("fixture_unavailable") from None

    parts = recorded.rstrip("\n").split("  ")
    if len(parts) != 2 or parts[1] != path.name:
        raise FixtureValidationError("invalid_fixture_checksum")
    expected_digest = parts[0]
    if (
        len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest)
    ):
        raise FixtureValidationError("invalid_fixture_checksum")

    actual_digest = _sha256_bytes(encoded)
    if (
        expected_digest != FROZEN_FIXTURE_SHA256
        or actual_digest != expected_digest
    ):
        raise FixtureValidationError("fixture_hash_mismatch")
    return encoded, actual_digest


def load_fixture(
    path: Path,
    *,
    checksum_path: Path | None = None,
) -> ValidatedIncidentSearchFixture:
    """Validate the frozen bytes, provenance, mapper output, and decision."""

    encoded, digest = _load_and_verify_fixture_bytes(path, checksum_path)
    try:
        decoded = json.loads(encoded)
        persisted = IncidentSearchReplayFixture.model_validate(decoded, strict=True)
        incident_input = IncidentInput.model_validate_json(
            persisted.incident_input.model_dump_json(),
            strict=True,
        )
        candidate = IncidentExtractionCandidate.model_validate_json(
            persisted.extraction_candidate.model_dump_json(),
            strict=True,
        )
        extraction_result = validate_incident_extraction(
            incident_input,
            candidate,
        )
        repository_hint = RepositoryHint(
            canonical_name=persisted.repository_hint.canonical_name,
            aliases=tuple(persisted.repository_hint.aliases),
        )
        tasks, state = map_incident_to_retrieval_context(
            incident_input,
            extraction_result,
            system_instruction=persisted.context_policy.system_instruction,
            repository_hints=(repository_hint,),
            selected_repository=persisted.selected_repository,
            evidence_item_budget=persisted.context_policy.evidence_item_budget,
            remaining_tool_calls=persisted.context_policy.remaining_tool_calls,
        )
        _validate_frozen_contract(persisted, incident_input, extraction_result, tasks)
        task = tasks[0]
        decision = ToolCallDecision(
            call_id=persisted.task_rule.call_id,
            tool_name=SEARCH_CODE,
            arguments=task.arguments.model_dump(mode="json"),
        )
    except (
        json.JSONDecodeError,
        ValidationError,
        IncidentExtractionValidationError,
        IncidentContextValidationError,
        TypeError,
        ValueError,
    ):
        raise FixtureValidationError("invalid_fixture_contract") from None

    return ValidatedIncidentSearchFixture(
        fixture_sha256=digest,
        contract_sha256=_canonical_json_sha256(
            persisted.model_dump(mode="json")
        ),
        persisted=persisted,
        incident_input=incident_input.model_copy(deep=True),
        extraction_result=extraction_result.model_copy(deep=True),
        tasks=tuple(item.model_copy(deep=True) for item in tasks),
        task=task.model_copy(deep=True),
        state=state.model_copy(deep=True),
        decision=decision.model_copy(deep=True),
    )


def _validate_frozen_contract(
    persisted: IncidentSearchReplayFixture,
    incident_input: IncidentInput,
    extraction_result: IncidentExtractionResult,
    tasks: tuple[IncidentRetrievalTask, ...],
) -> None:
    source_payload = incident_input.model_dump(mode="json")["sources"]
    if source_payload != [
        {
            "source_id": "log-001",
            "source_type": "log",
            "text": f"Error: {EXPECTED_QUERY}",
        }
    ]:
        raise ValueError("unexpected incident source")
    if persisted.case_id != EXPECTED_CASE_ID:
        raise ValueError("unexpected case id")
    if persisted.click_revision != PINNED_CLICK_REVISION:
        raise ValueError("unexpected Click revision")
    if persisted.repository_hint.model_dump(mode="json") != {
        "canonical_name": EXPECTED_REPOSITORY,
        "aliases": [],
    }:
        raise ValueError("unexpected repository hint")
    if persisted.selected_repository != EXPECTED_REPOSITORY:
        raise ValueError("unexpected repository selection")
    if persisted.task_rule.model_dump(mode="json") != {
        "task_count": 1,
        "field_name": "error_text",
        "source_ids": ["log-001"],
        "call_id": EXPECTED_CALL_ID,
    }:
        raise ValueError("unexpected task rule")

    observed = extraction_result.error_text
    if (
        extraction_result.exception_class is not None
        or extraction_result.method is not None
        or extraction_result.service_name is not None
        or extraction_result.configuration_keys
        or observed is None
        or observed.value != EXPECTED_QUERY
        or observed.source_ids != ("log-001",)
    ):
        raise ValueError("unexpected extraction result")
    if len(tasks) != 1:
        raise ValueError("unexpected task count")
    task = tasks[0]
    expected_arguments = persisted.search_arguments.model_dump(mode="json")
    if (
        task.field_name != "error_text"
        or task.query != EXPECTED_QUERY
        or task.source_ids != ("log-001",)
        or task.arguments.model_dump(mode="json") != expected_arguments
    ):
        raise ValueError("unexpected mapped task")
    SearchCodeArguments.model_validate(expected_arguments)

    gold = persisted.gold
    if gold.model_dump(mode="json") != {
        "repo": EXPECTED_REPOSITORY,
        "path": EXPECTED_GOLD_PATH,
        "line": EXPECTED_GOLD_LINE,
        "snippet": EXPECTED_GOLD_SNIPPET,
        "snippet_sha256": EXPECTED_GOLD_SNIPPET_SHA256,
    }:
        raise ValueError("unexpected gold")
    if _sha256_text(gold.snippet) != gold.snippet_sha256:
        raise ValueError("gold snippet hash mismatch")


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


def preflight_environment(
    fixture: ValidatedIncidentSearchFixture,
    *,
    repository_root: Path,
    declared_index_revision: str,
    zoekt_url_configured: bool,
) -> IncidentSearchPreflightReport:
    """Check only local identity and gold; this function never calls a Tool."""

    if not isinstance(fixture, ValidatedIncidentSearchFixture):
        raise TypeError("fixture must be a ValidatedIncidentSearchFixture")
    if not zoekt_url_configured:
        raise EnvironmentPreflightError("zoekt_url_missing")

    repository = (repository_root / EXPECTED_REPOSITORY).resolve()
    checkout_revision = _git_output(repository, "rev-parse", "HEAD")
    if checkout_revision != PINNED_CLICK_REVISION:
        raise EnvironmentPreflightError("checkout_revision_mismatch")
    if declared_index_revision != PINNED_CLICK_REVISION:
        raise EnvironmentPreflightError("index_revision_mismatch")
    zoekt_name = _git_output(repository, "config", "--get", "zoekt.name")
    if zoekt_name != EXPECTED_REPOSITORY:
        raise EnvironmentPreflightError("indexed_repository_name_mismatch")

    source = _git_output(
        repository,
        "show",
        f"{PINNED_CLICK_REVISION}:{fixture.persisted.gold.path}",
    )
    if source is None:
        raise EnvironmentPreflightError("gold_source_unavailable")
    source_lines = source.splitlines()
    line_index = fixture.persisted.gold.line - 1
    if line_index >= len(source_lines):
        raise EnvironmentPreflightError("gold_source_mismatch")
    source_line = source_lines[line_index].strip()
    if (
        source_line != fixture.persisted.gold.snippet
        or _sha256_text(source_line) != fixture.persisted.gold.snippet_sha256
    ):
        raise EnvironmentPreflightError("gold_source_mismatch")

    return IncidentSearchPreflightReport(
        status="passed",
        fixture_sha256=fixture.fixture_sha256,
        click_revision=PINNED_CLICK_REVISION,
        repository=EXPECTED_REPOSITORY,
        checks=PreflightChecksReport(
            zoekt_url_configured=True,
            checkout_revision=True,
            declared_index_revision=True,
            zoekt_name=True,
            gold_source_line=True,
        ),
    )


async def _await_if_needed(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


async def execute_replay(
    fixture: ValidatedIncidentSearchFixture,
    *,
    search_code: ToolHandler,
    get_file_context: ToolHandler | None = None,
) -> IncidentSearchReplayExecution:
    """Execute the one rebuilt search decision; every outcome stops the run."""

    if not isinstance(fixture, ValidatedIncidentSearchFixture):
        raise TypeError("fixture must be a ValidatedIncidentSearchFixture")
    _revalidate_execution_fixture(fixture)

    call_counts = {SEARCH_CODE: 0, GET_FILE_CONTEXT: 0}

    async def counted_search_code(**arguments: object) -> object:
        call_counts[SEARCH_CODE] += 1
        return await _await_if_needed(search_code(**arguments))

    async def fail_if_called_get_file_context(**arguments: object) -> object:
        call_counts[GET_FILE_CONTEXT] += 1
        if get_file_context is not None:
            return await _await_if_needed(get_file_context(**arguments))
        raise AssertionError("get_file_context is forbidden in Incident search replay")

    trace = TraceRecorder(task_id=fixture.persisted.case_id)
    router = ToolRouter(
        trace=trace,
        tools={
            SEARCH_CODE: PydanticToolAdapter(
                SearchCodeArguments,
                counted_search_code,
            ),
            GET_FILE_CONTEXT: PydanticToolAdapter(
                GetFileContextArguments,
                fail_if_called_get_file_context,
            ),
        },
    )
    outcome = await ToolStepExecutor(router=router).execute(
        fixture.state.model_copy(deep=True),
        fixture.decision.model_copy(deep=True),
    )
    report = _build_report(
        fixture,
        trace,
        outcome,
        search_code_calls=call_counts[SEARCH_CODE],
        get_file_context_calls=call_counts[GET_FILE_CONTEXT],
    )
    return IncidentSearchReplayExecution(
        fixture=fixture,
        trace=trace,
        outcome=outcome,
        report=report,
    )


def _revalidate_execution_fixture(
    fixture: ValidatedIncidentSearchFixture,
) -> None:
    """Detect nested in-memory mutation before the one permitted Tool call."""

    try:
        if fixture.fixture_sha256 != FROZEN_FIXTURE_SHA256:
            raise ValueError("fixture digest changed")
        if _canonical_json_sha256(
            fixture.persisted.model_dump(mode="json")
        ) != fixture.contract_sha256:
            raise ValueError("persisted contract changed")
        extraction_result = validate_incident_extraction(
            fixture.incident_input,
            IncidentExtractionCandidate.model_validate(
                fixture.extraction_result.model_dump(mode="python"),
                strict=True,
            ),
        )
        repository_hint = RepositoryHint(
            canonical_name=fixture.persisted.repository_hint.canonical_name,
            aliases=tuple(fixture.persisted.repository_hint.aliases),
        )
        tasks, state = map_incident_to_retrieval_context(
            fixture.incident_input,
            extraction_result,
            system_instruction=fixture.persisted.context_policy.system_instruction,
            repository_hints=(repository_hint,),
            selected_repository=fixture.persisted.selected_repository,
            evidence_item_budget=(
                fixture.persisted.context_policy.evidence_item_budget
            ),
            remaining_tool_calls=(
                fixture.persisted.context_policy.remaining_tool_calls
            ),
        )
        _validate_frozen_contract(
            fixture.persisted,
            fixture.incident_input,
            extraction_result,
            tasks,
        )
        expected_decision = ToolCallDecision(
            call_id=fixture.persisted.task_rule.call_id,
            tool_name=SEARCH_CODE,
            arguments=tasks[0].arguments.model_dump(mode="json"),
        )
        if (
            fixture.tasks != tasks
            or fixture.task != tasks[0]
            or fixture.state != state
            or fixture.decision != expected_decision
        ):
            raise ValueError("derived execution contract changed")
    except (ValidationError, TypeError, ValueError):
        raise FixtureValidationError("fixture_mutated_before_execution") from None


def _build_report(
    fixture: ValidatedIncidentSearchFixture,
    trace: TraceRecorder,
    outcome: ToolStepOutcome,
    *,
    search_code_calls: int,
    get_file_context_calls: int,
) -> IncidentSearchReplayReport:
    events = trace.events
    event_types = tuple(event.event_type for event in events)
    result = outcome.tool_result
    error_type: str | None = None
    gold_match: GoldMatchReport | None = None
    result_sha256: str | None = None

    if (
        len(events) != 2
        or not isinstance(events[0], ToolCall)
        or not isinstance(events[1], ToolResult)
        or event_types != ("tool_call", "tool_result")
        or result is None
        or events[1] != result
        or events[0].tool_name != SEARCH_CODE
        or events[0].call_id != fixture.decision.call_id
        or events[0].arguments != fixture.task.arguments.model_dump(mode="json")
    ):
        raise RuntimeError("incident replay trace invariant failed")
    if search_code_calls != 1 or get_file_context_calls != 0:
        raise RuntimeError("incident replay invocation invariant failed")
    if (
        fixture.state.remaining_tool_calls != 1
        or outcome.next_state.remaining_tool_calls != 0
        or outcome.next_model_input.remaining_tool_calls != 0
    ):
        raise RuntimeError("incident replay budget invariant failed")

    evidence_source = f"tool_result:{SEARCH_CODE}:{fixture.decision.call_id}"
    new_state_facts = tuple(
        item for item in outcome.next_state.evidence if item.source == evidence_source
    )
    new_input_facts = tuple(
        item
        for item in outcome.next_model_input.evidence
        if item.source == evidence_source
    )
    original_facts_preserved = all(
        isinstance(item, Evidence)
        and item in outcome.next_state.evidence
        and item in outcome.next_model_input.evidence
        for item in fixture.state.evidence
    )
    state_checks = StateChecksReport(
        new_tool_fact_in_next_state=len(new_state_facts) == 1,
        new_tool_fact_in_next_model_input=len(new_input_facts) == 1,
        incident_source_fact_preserved=original_facts_preserved,
    )
    if not all(state_checks.model_dump(mode="python").values()):
        raise RuntimeError("incident replay context invariant failed")

    if result.status == "error":
        error_type = result.error_type or "tool_execution_error"
    else:
        result_sha256 = _canonical_json_sha256(result.result)
        error_type, gold_match = _inspect_success_result(fixture, result.result)

    return IncidentSearchReplayReport(
        status="success" if error_type is None else "error",
        error_type=error_type,
        case_id=fixture.persisted.case_id,
        fixture_sha256=fixture.fixture_sha256,
        click_revision=fixture.persisted.click_revision,
        repository=fixture.persisted.selected_repository,
        query_sha256=_sha256_text(fixture.task.query),
        calls=CallCountReport(
            search_code=search_code_calls,
            get_file_context=get_file_context_calls,
            model=0,
            agent_loop=0,
        ),
        budget=BudgetReport(
            before=fixture.state.remaining_tool_calls,
            after=outcome.next_state.remaining_tool_calls,
        ),
        trace_event_types=event_types,
        tool_result_status=result.status,
        tool_result_error_type=result.error_type,
        tool_result_sha256=result_sha256,
        gold_match=gold_match,
        state_checks=state_checks,
    )


def _inspect_success_result(
    fixture: ValidatedIncidentSearchFixture,
    result: object,
) -> tuple[str | None, GoldMatchReport | None]:
    if not isinstance(result, Mapping):
        return "unexpected_result", None
    if result.get("query") != fixture.task.query:
        return "unexpected_result", None
    duration_ms = result.get("duration_ms")
    matches = result.get("matches")
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms < 0
        or not isinstance(matches, list)
    ):
        return "unexpected_result", None

    gold = fixture.persisted.gold
    for rank, match in enumerate(matches, start=1):
        if not isinstance(match, Mapping):
            return "unexpected_result", None
        if not all(
            key in match for key in ("repo", "path", "line", "snippet")
        ):
            return "unexpected_result", None
        if (
            match.get("repo") == gold.repo
            and match.get("path") == gold.path
            and match.get("line") == gold.line
            and match.get("snippet") == gold.snippet
        ):
            return None, GoldMatchReport(
                rank=rank,
                repo=gold.repo,
                path=gold.path,
                line=gold.line,
                snippet_sha256=gold.snippet_sha256,
            )
    return "gold_not_found", None


def _required_environment(name: str, error_type: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise EnvironmentPreflightError(error_type)
    return value


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or execute one frozen public Incident search_code task."
        )
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
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
        fixture = load_fixture(args.fixture)
    except FixtureValidationError as exc:
        _print_error(exc.error_type)
        return 2

    if args.validate_only:
        _print_json(
            {
                "status": "valid",
                "case_id": fixture.persisted.case_id,
                "fixture_sha256": fixture.fixture_sha256,
                "click_revision": fixture.persisted.click_revision,
                "repository": fixture.persisted.selected_repository,
                "task_count": len(fixture.tasks),
                "tool_calls": 0,
                "model_calls": 0,
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
        zoekt_url = _required_environment("ZOEKT_URL", "zoekt_url_missing")
        repository_root_value = _required_environment(
            "REPOSITORY_ROOT",
            "repository_root_missing",
        )
        declared_index_revision = _required_environment(
            "ZOEKT_INDEX_REVISION",
            "index_revision_missing",
        )
        preflight = preflight_environment(
            fixture,
            repository_root=Path(repository_root_value).expanduser().resolve(),
            declared_index_revision=declared_index_revision,
            zoekt_url_configured=bool(zoekt_url),
        )
    except EnvironmentPreflightError as exc:
        _print_error(exc.error_type)
        return 2

    if args.preflight_only:
        _print_json(preflight.model_dump(mode="json"))
        return 0

    try:
        from src.server import search_code

        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        execution = asyncio.run(
            execute_replay(
                fixture,
                search_code=search_code,
            )
        )
        artifact = IncidentSearchExecutionArtifact(
            preflight=preflight,
            replay=execution.report,
        )
        encoded = json.dumps(
            artifact.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        assert args.out is not None
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as output_file:
            output_file.write(encoded)
    except FileExistsError:
        _print_error("output_already_exists")
        return 4
    except Exception:
        _print_error("replay_failed")
        return 4

    _print_json(artifact.model_dump(mode="json"))
    return 0 if execution.report.status == "success" else 3


if __name__ == "__main__":
    raise SystemExit(main())
