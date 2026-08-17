#!/usr/bin/env python3
"""Validate and run the frozen Click agent-level evaluation exactly once."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, TypeAlias
from urllib.parse import urlparse
from uuid import uuid4

from openai import OpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from evaluation.run_eval import load_cases as load_retrieval_cases
from src.examples.code_understanding_agent import (
    AgentLoop,
    ContextBuilder,
    ContextState,
    FinalAnswer,
    GET_FILE_CONTEXT,
    GetFileContextArguments,
    ModelClient,
    ModelRequest,
    ModelResult,
    OpenAIModel,
    PydanticToolAdapter,
    RepositoryAliasResolver,
    RepositoryHint,
    SEARCH_CODE,
    SearchCodeArguments,
    ToolCall,
    ToolResult,
    ToolRouter,
    ToolStepExecutor,
    TraceRecorder,
    TracedModelClient,
    collect_allowed_final_answer_evidence,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = PROJECT_ROOT / "evaluation/agent_cases.jsonl"
PINNED_CLICK_REVISION = "6eeb50e948ea136db145280f6f5dd52eca3fa7e5"
FROZEN_DATASET_SHA256 = (
    "09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129"
)
MINIMUM_CASES = 10
MAXIMUM_TOOL_ATTEMPTS = 6

SYSTEM_INSTRUCTION = (
    "Inspect only the supplied Click repository with the retrieval tools. "
    "Do not answer code questions from memory or caller narration. Use "
    "search_code to find relevant source and get_file_context when surrounding "
    "implementation is needed. Every factual final answer must include one or "
    "more exact citation objects copied from successful Tool results. Each "
    "citation must contain only repo, path, and a positive integer line. If no "
    "successful Tool evidence supports an answer, return a final answer with "
    "an empty evidence list."
)

FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "base_url",
        "default_headers",
        "headers",
        "raw_exception",
        "raw_provider_response",
        "repository_root",
    }
)

FailureCategory: TypeAlias = Literal[
    "retrieval_no_match",
    "tool_or_service_unavailable",
    "harness_constraint",
    "insufficient_context",
    "model_judgment_failure",
    "evaluator_problem",
]

ToolHandler = Callable[..., object | Awaitable[object]]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GoldLocation(_StrictModel):
    repo: str = Field(min_length=1)
    path: str = Field(min_length=1)
    line_min: int = Field(ge=1)
    line_max: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "GoldLocation":
        if self.line_min > self.line_max:
            raise ValueError("line_min cannot exceed line_max")
        if self.repo != self.repo.strip() or self.path != self.path.strip():
            raise ValueError("gold repository and path must be trimmed")
        return self


class AbsenceScope(_StrictModel):
    repo: str = Field(min_length=1)
    path: str | None
    query: str = Field(min_length=1)


class AnswerableExpected(_StrictModel):
    result: Literal["answerable"]
    locations: tuple[GoldLocation, ...] = Field(min_length=1)
    review_note: str = Field(min_length=1)


class InsufficientExpected(_StrictModel):
    result: Literal["insufficient_evidence"]
    locations: tuple[GoldLocation, ...]
    absence_scope: AbsenceScope
    review_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_empty_locations(self) -> "InsufficientExpected":
        if self.locations:
            raise ValueError("insufficient-evidence gold cannot contain locations")
        return self


ExpectedResult = Annotated[
    AnswerableExpected | InsufficientExpected,
    Field(discriminator="result"),
]


class AgentEvalCase(_StrictModel):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    category: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected: ExpectedResult
    click_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    reviewed_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def validate_pinned_revision(self) -> "AgentEvalCase":
        if self.click_revision != PINNED_CLICK_REVISION:
            raise ValueError("case does not use the pinned Click revision")
        return self


class AgentEvalDataError(ValueError):
    """Signal a stable frozen-data validation failure."""


class EnvironmentPreflightError(RuntimeError):
    """Signal an unmet real-evaluation dependency without backend detail."""


def resolve_project_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return path.name


def dataset_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_agent_cases(path: Path) -> tuple[AgentEvalCase, ...]:
    """Load the complete frozen JSONL set and enforce its distribution."""

    if not path.is_file():
        raise AgentEvalDataError("agent evaluation cases file does not exist")

    cases: list[AgentEvalCase] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if not raw_line.strip():
                raise AgentEvalDataError(
                    f"line {line_number}: blank lines are not allowed"
                )
            try:
                cases.append(AgentEvalCase.model_validate_json(raw_line))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise AgentEvalDataError(
                    f"line {line_number}: invalid agent evaluation case"
                ) from exc

    if len(cases) < MINIMUM_CASES:
        raise AgentEvalDataError(
            f"agent evaluation requires at least {MINIMUM_CASES} cases"
        )
    ids = [case.id for case in cases]
    questions = [case.question for case in cases]
    if len(set(ids)) != len(ids):
        raise AgentEvalDataError("agent evaluation case ids must be unique")
    if len(set(questions)) != len(questions):
        raise AgentEvalDataError("agent evaluation questions must be unique")

    result_counts = Counter(case.expected.result for case in cases)
    if result_counts != {"answerable": 8, "insufficient_evidence": 2}:
        raise AgentEvalDataError(
            "frozen agent evaluation must contain 8 answerable and "
            "2 insufficient-evidence cases"
        )
    return tuple(cases)


def validate_frozen_dataset(path: Path) -> tuple[tuple[AgentEvalCase, ...], str]:
    cases = load_agent_cases(path)
    digest = dataset_sha256(path)
    if digest != FROZEN_DATASET_SHA256:
        raise AgentEvalDataError("agent evaluation data hash does not match freeze")
    return cases, digest


def configure_no_proxy_for_base_url(base_url: str) -> None:
    hostname = urlparse(base_url).hostname
    if hostname is None or not hostname.strip():
        raise EnvironmentPreflightError("model_endpoint_configuration_invalid")

    for variable_name in ("NO_PROXY", "no_proxy"):
        existing = [
            entry.strip()
            for entry in os.environ.get(variable_name, "").split(",")
            if entry.strip()
        ]
        if hostname not in existing:
            existing.append(hostname)
        os.environ[variable_name] = ",".join(existing)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise EnvironmentPreflightError(f"required_environment_missing:{name}")
    return value


def _git_output(repository: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = result.stdout.strip()
    return output if result.returncode == 0 and output else None


def _normalized_tool_payload(value: object) -> dict[str, Any] | None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return value if isinstance(value, dict) else None


async def preflight_environment(
    *,
    repository: Path,
    declared_index_revision: str,
    search_code: ToolHandler,
) -> dict[str, Any]:
    """Verify the checkout and pinned-content index probes before model calls."""

    checkout_revision = _git_output(repository, "rev-parse", "HEAD")
    zoekt_name = _git_output(repository, "config", "--get", "zoekt.name")
    if checkout_revision != PINNED_CLICK_REVISION:
        raise EnvironmentPreflightError("checkout_revision_mismatch")
    if declared_index_revision != PINNED_CLICK_REVISION:
        raise EnvironmentPreflightError("index_revision_mismatch")
    if zoekt_name != "click":
        raise EnvironmentPreflightError("indexed_repository_name_mismatch")

    probes = (
        {
            "id": "echo-definition",
            "arguments": {
                "query": r"def echo\(",
                "repo": "click",
                "lang": "python",
                "path": r"src/click/utils\.py",
                "limit": 10,
                "literal": True,
            },
            "expected": ("click", "src/click/utils.py", 234),
        },
        {
            "id": "choice-message",
            "arguments": {
                "query": r"def get_missing_message\(",
                "repo": "click",
                "lang": "python",
                "path": r"src/click/types\.py",
                "limit": 10,
                "literal": True,
            },
            "expected": ("click", "src/click/types.py", 358),
        },
        {
            "id": "negative-symbol",
            "arguments": {
                "query": "ClickOptionRegistry",
                "repo": "click",
                "lang": "python",
                "path": None,
                "limit": 10,
                "literal": True,
            },
            "expected": None,
        },
    )
    probe_reports: list[dict[str, Any]] = []
    for probe in probes:
        try:
            response = search_code(**probe["arguments"])
            if isinstance(response, Awaitable):
                response = await response
        except Exception:
            raise EnvironmentPreflightError("index_probe_service_unavailable") from None
        payload = _normalized_tool_payload(response)
        matches = payload.get("matches") if payload is not None else None
        if not isinstance(matches, list):
            raise EnvironmentPreflightError("index_probe_invalid_response")

        expected = probe["expected"]
        if expected is None:
            passed = not matches
        else:
            expected_repo, expected_path, expected_line = expected
            passed = any(
                isinstance(match, dict)
                and match.get("repo") == expected_repo
                and match.get("path") == expected_path
                and match.get("line") == expected_line
                for match in matches
            )
        probe_reports.append({"id": probe["id"], "passed": passed})
        if not passed:
            raise EnvironmentPreflightError("index_pinned_content_probe_failed")

    retrieval_cases = load_retrieval_cases(
        PROJECT_ROOT / "evaluation/cases.jsonl"
    )
    if len(retrieval_cases) != 18:
        raise EnvironmentPreflightError("retrieval_dataset_validation_failed")

    return {
        "checkout_revision": checkout_revision,
        "declared_index_revision": declared_index_revision,
        "repository_zoekt_name": zoekt_name,
        "index_revision_check": {
            "status": "passed",
            "method": (
                "operator-declared revision plus pinned positive and negative "
                "content probes"
            ),
            "server_commit_exposed": False,
        },
        "pinned_content_probes": probe_reports,
        "retrieval_dataset_validation": {
            "status": "passed",
            "case_count": len(retrieval_cases),
        },
    }


def _elapsed_ms(started_at: float, finished_at: float) -> int:
    return max(0, round((finished_at - started_at) * 1000))


def _timed_tool_handlers(
    *,
    search_code: ToolHandler,
    get_file_context: ToolHandler,
    timings: list[dict[str, Any]],
    clock: Callable[[], float],
) -> tuple[ToolHandler, ToolHandler]:
    async def timed_search(**arguments: object) -> object:
        started_at = clock()
        try:
            result = search_code(**arguments)
            if isinstance(result, Awaitable):
                result = await result
            return result
        finally:
            timings.append(
                {
                    "tool_name": SEARCH_CODE,
                    "elapsed_ms": _elapsed_ms(started_at, clock()),
                }
            )

    async def timed_context(**arguments: object) -> object:
        started_at = clock()
        try:
            result = get_file_context(**arguments)
            if isinstance(result, Awaitable):
                result = await result
            return result
        finally:
            timings.append(
                {
                    "tool_name": GET_FILE_CONTEXT,
                    "elapsed_ms": _elapsed_ms(started_at, clock()),
                }
            )

    return timed_search, timed_context


def analyze_trace(trace: TraceRecorder) -> dict[str, Any]:
    """Recompute the required replay invariants from recorded events."""

    events = trace.events
    task_ids_match = all(event.task_id == trace.task_id for event in events)
    sequences_continuous = [event.sequence for event in events] == list(
        range(1, len(events) + 1)
    )
    final_indexes = [
        index for index, event in enumerate(events) if isinstance(event, FinalAnswer)
    ]
    unique_terminal = len(final_indexes) == 1
    terminal_last = unique_terminal and final_indexes[0] == len(events) - 1
    finalized_matches = trace.is_finalized == unique_terminal

    model_pairs_complete = True
    tool_pairs_complete = True
    for index, event in enumerate(events):
        if isinstance(event, ModelRequest):
            if index + 1 >= len(events):
                model_pairs_complete = False
                continue
            result = events[index + 1]
            if not isinstance(result, ModelResult):
                model_pairs_complete = False
            elif result.request_id != event.request_id:
                model_pairs_complete = False
        elif isinstance(event, ModelResult):
            if index == 0:
                model_pairs_complete = False
                continue
            request = events[index - 1]
            if not isinstance(request, ModelRequest):
                model_pairs_complete = False
            elif request.request_id != event.request_id:
                model_pairs_complete = False
        elif isinstance(event, ToolCall):
            if index + 1 >= len(events):
                tool_pairs_complete = False
                continue
            result = events[index + 1]
            if not isinstance(result, ToolResult):
                tool_pairs_complete = False
            elif result.call_id != event.call_id:
                tool_pairs_complete = False
        elif isinstance(event, ToolResult):
            if index == 0:
                tool_pairs_complete = False
                continue
            call = events[index - 1]
            if not isinstance(call, ToolCall):
                tool_pairs_complete = False
            elif call.call_id != event.call_id:
                tool_pairs_complete = False

    checks = {
        "task_ids_match": task_ids_match,
        "sequences_continuous": sequences_continuous,
        "model_pairs_complete": model_pairs_complete,
        "tool_pairs_complete": tool_pairs_complete,
        "unique_terminal": unique_terminal,
        "terminal_last": terminal_last,
        "finalized_matches_terminal": finalized_matches,
    }
    return {"ok": all(checks.values()), **checks}


def _submitted_final_decision(trace: TraceRecorder) -> dict[str, Any] | None:
    submitted: dict[str, Any] | None = None
    for event in trace.events:
        if not isinstance(event, ModelResult) or event.status != "success":
            continue
        decision = event.decision
        if not isinstance(decision, dict):
            continue
        if decision.get("decision_type") == "final_answer":
            submitted = decision
    return submitted


def parse_citation(citation: str) -> dict[str, Any]:
    """Parse an eval citation; provenance validation remains exact-set based."""

    if not isinstance(citation, str) or not citation:
        return {"ok": False, "error": "not_non_empty_text"}
    if ":" not in citation:
        return {"ok": False, "error": "missing_line_separator"}
    location, line_text = citation.rsplit(":", 1)
    if not re.fullmatch(r"[1-9][0-9]*", line_text):
        return {"ok": False, "error": "invalid_positive_line"}
    if "/" not in location:
        return {"ok": False, "error": "missing_repository_path_separator"}
    repository, path = location.split("/", 1)
    if (
        not repository
        or not path
        or repository != repository.strip()
        or path != path.strip()
        or path.startswith("/")
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        return {"ok": False, "error": "invalid_repository_or_path"}
    return {
        "ok": True,
        "repo": repository,
        "path": path,
        "line": int(line_text),
    }


def _matches_gold(parsed: dict[str, Any], case: AgentEvalCase) -> bool:
    if not parsed.get("ok"):
        return False
    return any(
        parsed.get("repo") == location.repo
        and parsed.get("path") == location.path
        and location.line_min <= parsed.get("line", 0) <= location.line_max
        for location in case.expected.locations
    )


def analyze_submitted_citations(
    *,
    case: AgentEvalCase,
    trace: TraceRecorder,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    decision = _submitted_final_decision(trace)
    raw_evidence = decision.get("evidence", []) if decision is not None else []
    submitted = (
        tuple(item for item in raw_evidence if isinstance(item, str))
        if isinstance(raw_evidence, list)
        else ()
    )
    allowed = collect_allowed_final_answer_evidence(
        trace.events,
        task_id=trace.task_id,
    )
    reports: list[dict[str, Any]] = []
    for citation in submitted:
        parsed = parse_citation(citation)
        reports.append(
            {
                "citation": citation,
                "parse": parsed,
                "from_current_successful_tool_fact": citation in allowed,
                "gold_match": _matches_gold(parsed, case),
            }
        )
    return reports, submitted


def _search_had_no_matches(trace: TraceRecorder) -> bool:
    successful_search_results = 0
    for index, event in enumerate(trace.events):
        if not isinstance(event, ToolCall) or event.tool_name != SEARCH_CODE:
            continue
        if index + 1 >= len(trace.events):
            continue
        result = trace.events[index + 1]
        if not isinstance(result, ToolResult) or result.status != "success":
            continue
        successful_search_results += 1
        payload = result.result
        if isinstance(payload, dict) and isinstance(payload.get("matches"), list):
            if payload["matches"]:
                return False
    return successful_search_results > 0


def _latency_metric(values: Iterable[int]) -> dict[str, Any]:
    measured = list(values)
    if not measured:
        return {
            "count": 0,
            "minimum": None,
            "maximum": None,
            "average": None,
            "total": None,
        }
    total = sum(measured)
    return {
        "count": len(measured),
        "minimum": min(measured),
        "maximum": max(measured),
        "average": total / len(measured),
        "total": total,
    }


def _case_success(
    *,
    case: AgentEvalCase,
    final_answer: FinalAnswer | None,
    submitted: tuple[str, ...],
    citation_reports: Sequence[dict[str, Any]],
    trace_integrity: dict[str, Any],
    tool_attempts: int,
    evaluation_error: str | None,
) -> bool:
    if evaluation_error is not None:
        return False
    if not trace_integrity["ok"] or tool_attempts > MAXIMUM_TOOL_ATTEMPTS:
        return False
    if final_answer is None:
        return False

    if case.expected.result == "answerable":
        return (
            final_answer.termination_reason == "completed"
            and bool(submitted)
            and all(
                report["from_current_successful_tool_fact"]
                for report in citation_reports
            )
            and any(report["gold_match"] for report in citation_reports)
        )

    return (
        final_answer.termination_reason == "insufficient_evidence"
        and final_answer.evidence == []
        and not submitted
    )


def _failure_category(
    *,
    success: bool,
    case: AgentEvalCase,
    final_answer: FinalAnswer | None,
    trace_integrity: dict[str, Any],
    tool_attempts: int,
    evaluation_error: str | None,
    search_had_no_matches: bool,
    citation_reports: Sequence[dict[str, Any]],
) -> FailureCategory | None:
    if success:
        return None
    if evaluation_error is not None or final_answer is None:
        return "evaluator_problem"
    if not trace_integrity["ok"] or tool_attempts > MAXIMUM_TOOL_ATTEMPTS:
        return "harness_constraint"
    reason = final_answer.termination_reason
    if reason in {"tool_error", "tool_timeout", "model_execution_error", "model_timeout"}:
        return "tool_or_service_unavailable"
    if reason in {"tool_budget_exhausted", "harness_invariant_error"}:
        return "harness_constraint"
    if reason == "invalid_model_output":
        return "model_judgment_failure"
    if citation_reports and any(
        not citation["from_current_successful_tool_fact"]
        or not citation["gold_match"]
        for citation in citation_reports
    ):
        return "model_judgment_failure"
    if case.expected.result == "answerable" and search_had_no_matches:
        return "retrieval_no_match"
    if case.expected.result == "answerable" and reason == "insufficient_evidence":
        return "insufficient_context"
    return "model_judgment_failure"


async def evaluate_agent_case(
    case: AgentEvalCase,
    *,
    model: ModelClient,
    model_name: str,
    search_code: ToolHandler,
    get_file_context: ToolHandler,
    task_id: str | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Run one complete AgentLoop and return a recomputable safe record."""

    if task_id is None:
        task_id = f"agent-eval-{case.id}-{uuid4()}"
    trace = TraceRecorder(task_id=task_id)
    context_builder = ContextBuilder()
    tool_timings: list[dict[str, Any]] = []
    timed_search, timed_context = _timed_tool_handlers(
        search_code=search_code,
        get_file_context=get_file_context,
        timings=tool_timings,
        clock=clock,
    )
    resolver = RepositoryAliasResolver(
        (
            RepositoryHint(
                canonical_name="click",
                aliases=(
                    "Click",
                    "pallets/click",
                    "github.com/pallets/click",
                ),
            ),
        )
    )
    router = ToolRouter(
        trace=trace,
        tools={
            SEARCH_CODE: PydanticToolAdapter(SearchCodeArguments, timed_search),
            GET_FILE_CONTEXT: PydanticToolAdapter(
                GetFileContextArguments,
                timed_context,
            ),
        },
        call_resolver=resolver,
    )
    traced_model = TracedModelClient(
        client=model,
        model=model_name,
        trace=trace,
    )
    loop = AgentLoop(
        context_builder=context_builder,
        model=traced_model,
        tool_step_executor=ToolStepExecutor(
            router=router,
            context_builder=context_builder,
        ),
        trace=trace,
    )
    state = ContextState(
        system_instruction=SYSTEM_INSTRUCTION,
        current_task=case.question,
        repository_hints=(
            RepositoryHint(
                canonical_name="click",
                aliases=(
                    "Click",
                    "pallets/click",
                    "github.com/pallets/click",
                ),
            ),
        ),
        evidence=(),
        evidence_item_budget=MAXIMUM_TOOL_ATTEMPTS,
        remaining_tool_calls=MAXIMUM_TOOL_ATTEMPTS,
    )

    outcome = None
    evaluation_error: str | None = None
    started_at = clock()
    try:
        outcome = await loop.run(state)
    except Exception:
        evaluation_error = "unexpected_evaluator_exception"
    total_elapsed_ms = _elapsed_ms(started_at, clock())

    final_answer = outcome.final_answer if outcome is not None else None
    tool_calls = [event for event in trace.events if isinstance(event, ToolCall)]
    tool_attempts = (
        outcome.tool_calls_used if outcome is not None else len(tool_calls)
    )
    trace_integrity = analyze_trace(trace)
    citation_reports, submitted = analyze_submitted_citations(
        case=case,
        trace=trace,
    )
    success = _case_success(
        case=case,
        final_answer=final_answer,
        submitted=submitted,
        citation_reports=citation_reports,
        trace_integrity=trace_integrity,
        tool_attempts=tool_attempts,
        evaluation_error=evaluation_error,
    )
    search_had_no_matches = _search_had_no_matches(trace)
    failure_category = _failure_category(
        success=success,
        case=case,
        final_answer=final_answer,
        trace_integrity=trace_integrity,
        tool_attempts=tool_attempts,
        evaluation_error=evaluation_error,
        search_had_no_matches=search_had_no_matches,
        citation_reports=citation_reports,
    )
    model_elapsed_values = [
        event.elapsed_ms
        for event in trace.events
        if isinstance(event, ModelResult)
    ]

    return {
        "case_id": case.id,
        "category": case.category,
        "question": case.question,
        "gold": {
            "expected_result": case.expected.result,
            "locations": [
                location.model_dump(mode="json")
                for location in case.expected.locations
            ],
            "click_revision": case.click_revision,
            "reviewed_on": case.reviewed_on,
        },
        "task_id": trace.task_id,
        "success": success,
        "failure_category": failure_category,
        "evaluation_error": evaluation_error,
        "final_answer": (
            final_answer.model_dump(mode="json")
            if final_answer is not None
            else None
        ),
        "submitted_final_decision": _submitted_final_decision(trace),
        "citations": citation_reports,
        "tool_attempts": {
            "count": tool_attempts,
            "names": [call.tool_name for call in tool_calls],
            "within_limit": tool_attempts <= MAXIMUM_TOOL_ATTEMPTS,
            "limit": MAXIMUM_TOOL_ATTEMPTS,
        },
        "trace_integrity": trace_integrity,
        "latency_ms": {
            "model_calls": model_elapsed_values,
            "model": _latency_metric(model_elapsed_values),
            "tools": tool_timings,
            "tool": _latency_metric(
                timing["elapsed_ms"] for timing in tool_timings
            ),
            "task_total": total_elapsed_ms,
        },
        "retrieval_no_match_observed": search_had_no_matches,
        "trace": trace.to_dict(),
    }


def build_agent_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Recompute all aggregate Agent Eval metrics from case detail records."""

    total = len(records)
    successful = sum(bool(record["success"]) for record in records)
    termination_reasons = Counter(
        (
            record["final_answer"]["termination_reason"]
            if isinstance(record.get("final_answer"), dict)
            else "missing_terminal"
        )
        for record in records
    )
    tool_counts = [record["tool_attempts"]["count"] for record in records]
    tool_distribution = Counter(tool_counts)
    citation_reports = [
        citation
        for record in records
        for citation in record.get("citations", [])
    ]
    valid_citations = sum(
        bool(citation["from_current_successful_tool_fact"])
        for citation in citation_reports
    )
    total_citations = len(citation_reports)
    trace_complete = sum(
        bool(record["trace_integrity"]["ok"]) for record in records
    )
    failure_cases: dict[str, list[str]] = {}
    for record in records:
        category = record.get("failure_category")
        if category is None:
            continue
        failure_cases.setdefault(category, []).append(record["case_id"])

    model_call_latencies = [
        latency
        for record in records
        for latency in record["latency_ms"]["model_calls"]
    ]
    task_latencies = [record["latency_ms"]["task_total"] for record in records]
    tool_latencies = [
        timing["elapsed_ms"]
        for record in records
        for timing in record["latency_ms"]["tools"]
    ]
    citation_rate = (
        valid_citations / total_citations if total_citations else None
    )
    maximum_tools = max(tool_counts) if tool_counts else None
    all_within_tool_limit = all(
        record["tool_attempts"]["within_limit"] for record in records
    )
    agent_gate_met = (
        total >= MINIMUM_CASES
        and all_within_tool_limit
        and trace_complete == total
        and total_citations > 0
        and citation_rate is not None
        and citation_rate >= 0.95
    )

    return {
        "task_success_rate": {
            "numerator": successful,
            "denominator": total,
            "rate": successful / total if total else None,
        },
        "termination_reason_distribution": dict(sorted(termination_reasons.items())),
        "tool_attempts": {
            "average": sum(tool_counts) / total if total else None,
            "maximum": maximum_tools,
            "distribution": {
                str(count): frequency
                for count, frequency in sorted(tool_distribution.items())
            },
            "within_limit": sum(
                bool(record["tool_attempts"]["within_limit"])
                for record in records
            ),
            "denominator": total,
        },
        "citation_validity": {
            "numerator": valid_citations,
            "denominator": total_citations,
            "rate": citation_rate,
        },
        "trace_integrity": {
            "numerator": trace_complete,
            "denominator": total,
            "rate": trace_complete / total if total else None,
        },
        "failures": {
            category: {"count": len(case_ids), "case_ids": case_ids}
            for category, case_ids in sorted(failure_cases.items())
        },
        "latency_ms": {
            "model_calls": _latency_metric(model_call_latencies),
            "tools": _latency_metric(tool_latencies),
            "tasks": _latency_metric(task_latencies),
        },
        "agent_gate": {
            "minimum_cases_met": total >= MINIMUM_CASES,
            "tool_limit_met": all_within_tool_limit,
            "all_traces_complete": trace_complete == total,
            "citation_rate_threshold": 0.95,
            "citation_rate_met": (
                citation_rate is not None and citation_rate >= 0.95
            ),
            "met": agent_gate_met,
        },
    }


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        if any(str(key).lower() in FORBIDDEN_REPORT_KEYS for key in value):
            return True
        return any(_contains_forbidden_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def assert_report_is_sanitized(
    report: dict[str, Any],
    *,
    sensitive_values: Iterable[str],
) -> dict[str, bool]:
    serialized = json.dumps(
        report,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    configured_values_absent = all(
        not value or value not in serialized for value in sensitive_values
    )
    forbidden_keys_absent = not _contains_forbidden_key(report)
    if not configured_values_absent or not forbidden_keys_absent:
        raise RuntimeError("report_sanitization_failed")
    return {
        "configured_sensitive_values_absent": configured_values_absent,
        "transport_and_local_path_fields_absent": forbidden_keys_absent,
    }


def build_report(
    *,
    cases_path: Path,
    dataset_digest: str,
    cases: Sequence[AgentEvalCase],
    records: Sequence[dict[str, Any]],
    preflight: dict[str, Any],
    model_name: str,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    summary = build_agent_summary(records)
    preflight_passed = (
        preflight["index_revision_check"]["status"] == "passed"
        and preflight["retrieval_dataset_validation"]["status"] == "passed"
        and all(probe["passed"] for probe in preflight["pinned_content_probes"])
    )
    m3_exit_criteria_met = summary["agent_gate"]["met"] and preflight_passed
    return {
        "schema_version": 1,
        "evaluation": "frozen_click_agent_eval",
        "generated_at": finished_at.isoformat(),
        "dataset": {
            "path": display_path(cases_path),
            "sha256": dataset_digest,
            "case_count": len(cases),
            "answerable_cases": sum(
                case.expected.result == "answerable" for case in cases
            ),
            "insufficient_evidence_cases": sum(
                case.expected.result == "insufficient_evidence" for case in cases
            ),
            "click_revision": PINNED_CLICK_REVISION,
            "frozen_before_model_calls": True,
        },
        "execution": {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "case_execution": "sequential_one_pass",
            "reruns_or_best_of_selection": False,
            "model": model_name,
            "tool_attempt_limit_per_case": MAXIMUM_TOOL_ATTEMPTS,
            "preflight": preflight,
        },
        "summary": summary,
        "verdict": {
            "m3_exit_criteria_met": m3_exit_criteria_met,
            "task_success_has_no_prefilled_threshold": True,
            "index_server_commit_not_exposed": True,
        },
        "cases": list(records),
    }


def _format_ratio(metric: dict[str, Any]) -> str:
    numerator = metric["numerator"]
    denominator = metric["denominator"]
    rate = metric["rate"]
    if rate is None:
        return f"{numerator}/{denominator} (n/a)"
    return f"{numerator}/{denominator} ({rate:.2%})"


def render_markdown_report(report: dict[str, Any]) -> str:
    dataset = report["dataset"]
    summary = report["summary"]
    preflight = report["execution"]["preflight"]
    verdict = report["verdict"]
    lines = [
        "# Frozen Click Agent Eval — 2026-08-16",
        "",
        "## Verdict",
        "",
        (
            "M3 exit criteria are **met** for this frozen single-repository "
            "evaluation."
            if verdict["m3_exit_criteria_met"]
            else "M3 exit criteria are **not met** for this evaluation."
        ),
        "",
        (
            f"The dataset was frozen before model calls at SHA-256 "
            f"`{dataset['sha256']}` with {dataset['case_count']} cases "
            f"(8 answerable, 2 expected information-insufficient) on Click "
            f"revision `{dataset['click_revision']}`. Every case was executed "
            "once in fixed file order; no best-of selection or result-driven "
            "Prompt, retrieval, or gold changes were made."
        ),
        "",
        "## Metrics",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        f"| Task success | {_format_ratio(summary['task_success_rate'])} |",
        f"| Citation validity | {_format_ratio(summary['citation_validity'])} |",
        f"| Trace integrity | {_format_ratio(summary['trace_integrity'])} |",
        (
            f"| Tool attempts | avg {summary['tool_attempts']['average']:.2f}, "
            f"max {summary['tool_attempts']['maximum']} |"
        ),
        "",
        "Termination reasons: `"
        + json.dumps(
            summary["termination_reason_distribution"],
            ensure_ascii=False,
            sort_keys=True,
        )
        + "`.",
        "",
        "Tool-attempt distribution: `"
        + json.dumps(
            summary["tool_attempts"]["distribution"],
            ensure_ascii=False,
            sort_keys=True,
        )
        + "`.",
        "",
        "Observed latency (milliseconds):",
        "",
        "- Model calls: `"
        + json.dumps(summary["latency_ms"]["model_calls"], sort_keys=True)
        + "`",
        "- Tools: `"
        + json.dumps(summary["latency_ms"]["tools"], sort_keys=True)
        + "`",
        "- Tasks: `"
        + json.dumps(summary["latency_ms"]["tasks"], sort_keys=True)
        + "`",
        "",
        "## Failure classification",
        "",
    ]
    if summary["failures"]:
        for category, detail in summary["failures"].items():
            lines.append(
                f"- `{category}`: {detail['count']} — "
                + ", ".join(f"`{case_id}`" for case_id in detail["case_ids"])
            )
    else:
        lines.append("No failed cases.")

    lines.extend(
        [
            "",
            "## Per-case results",
            "",
            "| Case | Expected | Terminal | Tools | Trace | Success | Failure |",
            "| --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for record in report["cases"]:
        final = record["final_answer"]
        terminal = final["termination_reason"] if final is not None else "missing"
        failure = record["failure_category"] or "—"
        lines.append(
            f"| `{record['case_id']}` | {record['gold']['expected_result']} | "
            f"`{terminal}` | {record['tool_attempts']['count']} | "
            f"{'pass' if record['trace_integrity']['ok'] else 'fail'} | "
            f"{'pass' if record['success'] else 'fail'} | `{failure}` |"
        )

    lines.extend(
        [
            "",
            "## Environment and honest boundary",
            "",
            (
                f"Checkout and declared index revision both matched "
                f"`{preflight['checkout_revision']}`; all "
                f"{len(preflight['pinned_content_probes'])} pinned content "
                "probes passed, `zoekt.name` was `click`, and the existing "
                "18-case retrieval data contract validated."
            ),
            "",
            (
                "The Zoekt server does not expose its build commit or an "
                "embedded source commit. Index consistency is therefore "
                "supported by the operator-declared pinned revision plus "
                "positive and negative content probes, not by a server-native "
                "revision field. Results apply only to this Click 8.4.1, "
                "Python, single-repository setup and are not a production or "
                "cross-language quality claim."
            ),
            "",
            (
                "The companion JSON preserves every sanitized case trace, "
                "submitted final decision, Tool sequence, citation provenance "
                "check, gold match, invariant result, timing, and raw stable "
                "failure classification needed to recompute these metrics."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the frozen Click Agent Eval once."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evaluation/agent_cases.jsonl"),
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--summary-out", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-proxy-base-url", action="store_true")
    return parser.parse_args(argv)


def _print_stable_error(error_type: str) -> None:
    print(
        json.dumps(
            {"status": "error", "error_type": error_type},
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    cases_path = resolve_project_path(args.cases)
    try:
        cases, digest = validate_frozen_dataset(cases_path)
    except (OSError, AgentEvalDataError):
        _print_stable_error("invalid_or_unfrozen_dataset")
        return 2

    if args.validate_only:
        print(
            json.dumps(
                {
                    "case_count": len(cases),
                    "click_revision": PINNED_CLICK_REVISION,
                    "dataset_sha256": digest,
                    "status": "valid",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    if cases_path != DEFAULT_CASES_PATH.resolve():
        _print_stable_error("real_eval_requires_default_frozen_dataset")
        return 2

    try:
        api_key = _required_environment("OPENAI_API_KEY")
        base_url = _required_environment("OPENAI_BASE_URL")
        model_name = _required_environment("OPENAI_MODEL")
        declared_index_revision = _required_environment("ZOEKT_INDEX_REVISION")
        if args.no_proxy_base_url:
            configure_no_proxy_for_base_url(base_url)

        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)

        from src.config import get_repository_root
        from src.server import get_file_context, search_code

        click_repository = get_repository_root("click")
        preflight = await preflight_environment(
            repository=click_repository,
            declared_index_revision=declared_index_revision,
            search_code=search_code,
        )
    except EnvironmentPreflightError as exc:
        _print_stable_error(str(exc))
        return 3
    except Exception:
        _print_stable_error("environment_preflight_failed")
        return 3

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=60.0,
    )
    model = OpenAIModel(client=client, model=model_name)
    started_at = datetime.now(timezone.utc)
    records: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        record = await evaluate_agent_case(
            case,
            model=model,
            model_name=model_name,
            search_code=search_code,
            get_file_context=get_file_context,
        )
        records.append(record)
        final = record["final_answer"]
        reason = final["termination_reason"] if final is not None else "missing"
        print(
            f"[{index}/{len(cases)}] {case.id} "
            f"success={int(record['success'])} terminal={reason} "
            f"tools={record['tool_attempts']['count']}"
        )

    finished_at = datetime.now(timezone.utc)
    report = build_report(
        cases_path=cases_path,
        dataset_digest=digest,
        cases=cases,
        records=records,
        preflight=preflight,
        model_name=model_name,
        started_at=started_at,
        finished_at=finished_at,
    )
    try:
        repository_path = str(click_repository.resolve())
        redaction = assert_report_is_sanitized(
            report,
            sensitive_values=(api_key, base_url, repository_path),
        )
        report["redaction_evidence"] = redaction
        assert_report_is_sanitized(
            report,
            sensitive_values=(api_key, base_url, repository_path),
        )
    except RuntimeError:
        _print_stable_error("report_sanitization_failed")
        return 4

    date_string = datetime.now().strftime("%Y-%m-%d")
    out_path = resolve_project_path(
        args.out
        or Path(f"evaluation/reports/agent-eval-{date_string}.json")
    )
    summary_path = resolve_project_path(
        args.summary_out
        or Path(f"evaluation/reports/agent-eval-{date_string}.md")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(render_markdown_report(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json_report": display_path(out_path),
                "markdown_report": display_path(summary_path),
                "m3_exit_criteria_met": report["verdict"][
                    "m3_exit_criteria_met"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        _print_stable_error("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
