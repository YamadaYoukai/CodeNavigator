#!/usr/bin/env python3
"""Run Code Search MCP retrieval evaluation.

Measures:

- Hit@1
- Hit@5
- search_code -> get_file_context closed-loop success
- Zoekt tool latency
- Python wall-clock latency
- per-case errors

This runner directly calls Python functions instead of MCP stdio transport.
That keeps the M2 retrieval baseline focused on search quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_base_repository, get_repository_root
from src.server import get_file_context, search_code


SEARCH_FIELDS = {
    "query",
    "repo",
    "lang",
    "path",
    "limit",
    "literal",
}

EXPECTED_FIELDS = {
    "outcome",
    "repo",
    "path",
    "line",
    "line_min",
    "line_max",
    "failure_reason",
    "context",
}

CONTEXT_EXPECTATION_FIELDS = {
    "start_line",
    "end_line",
    "total_lines",
    "truncated",
}

MATCH_OUTCOME = "match"
NO_MATCH_OUTCOME = "no_match"


class EvaluationCaseError(ValueError):
    """Raised when an evaluation case is malformed."""


def resolve_project_path(path: str | Path) -> Path:
    """Resolve relative paths against the MCP project root."""
    candidate = Path(path).expanduser()

    if candidate.is_absolute():
        return candidate.resolve()

    return (PROJECT_ROOT / candidate).resolve()


def display_path(path: Path) -> str:
    """Return a project-relative path when possible."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def model_to_dict(value: Any) -> dict[str, Any]:
    """Convert a Pydantic model or dict to a JSON-compatible dict."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    if isinstance(value, dict):
        return dict(value)

    raise TypeError(f"Unsupported result type: {type(value).__name__}")


def validate_integer(
        value: Any,
        field_name: str,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationCaseError(
            f"{field_name} must be an integer, got {value!r}"
        )

    if minimum is not None and value < minimum:
        raise EvaluationCaseError(
            f"{field_name} must be >= {minimum}, got {value}"
        )

    if maximum is not None and value > maximum:
        raise EvaluationCaseError(
            f"{field_name} must be <= {maximum}, got {value}"
        )


def validate_case(case: Any, line_number: int) -> dict[str, Any]:
    """Validate one JSONL evaluation case."""
    if not isinstance(case, dict):
        raise EvaluationCaseError(
            f"line {line_number}: case must be a JSON object"
        )

    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise EvaluationCaseError(
            f"line {line_number}: missing non-empty string field 'id'"
        )

    category = case.get("category")
    if not isinstance(category, str) or not category.strip():
        raise EvaluationCaseError(
            f"line {line_number}: missing non-empty string field 'category'"
        )

    search = case.get("search")
    if not isinstance(search, dict):
        raise EvaluationCaseError(
            f"line {line_number}: 'search' must be an object"
        )

    unknown_fields = set(search) - SEARCH_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise EvaluationCaseError(
            f"line {line_number}: unsupported search fields: {names}"
        )

    query = search.get("query")
    if not isinstance(query, str) or not query.strip():
        raise EvaluationCaseError(
            f"line {line_number}: search.query must be a non-empty string"
        )

    for field_name in ("repo", "lang", "path"):
        if field_name in search and search[field_name] is not None:
            if not isinstance(search[field_name], str):
                raise EvaluationCaseError(
                    f"line {line_number}: search.{field_name} must be a string"
                )

    if "limit" in search:
        validate_integer(
            search["limit"],
            f"line {line_number}: search.limit",
            minimum=1,
            maximum=100,
        )

    if "literal" in search and not isinstance(search["literal"], bool):
        raise EvaluationCaseError(
            f"line {line_number}: search.literal must be boolean"
        )

    validate_expected(case=case, line_number=line_number)

    return case


def validate_expected(case: dict[str, Any], line_number: int) -> None:
    """Validate and normalize the expected result for one evaluation case."""
    expected = case.get("expected")
    if not isinstance(expected, dict):
        raise EvaluationCaseError(
            f"line {line_number}: 'expected' must be an object"
        )

    unknown_fields = set(expected) - EXPECTED_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise EvaluationCaseError(
            f"line {line_number}: unsupported expected fields: {names}"
        )

    outcome = expected.get("outcome", MATCH_OUTCOME)
    if outcome not in {MATCH_OUTCOME, NO_MATCH_OUTCOME}:
        raise EvaluationCaseError(
            f"line {line_number}: expected.outcome must be "
            f"{MATCH_OUTCOME!r} or {NO_MATCH_OUTCOME!r}"
        )
    expected["outcome"] = outcome

    expected_repo = expected.get("repo")
    if not isinstance(expected_repo, str) or not expected_repo.strip():
        raise EvaluationCaseError(
            f"line {line_number}: expected.repo must be a non-empty string"
        )

    expected_path = expected.get("path")
    if not isinstance(expected_path, str) or not expected_path.strip():
        raise EvaluationCaseError(
            f"line {line_number}: expected.path must be a non-empty string"
        )

    if outcome == NO_MATCH_OUTCOME:
        if "line" not in expected or expected["line"] is not None:
            raise EvaluationCaseError(
                f"line {line_number}: expected.line must be null for no_match"
            )

        if "line_min" in expected or "line_max" in expected:
            raise EvaluationCaseError(
                f"line {line_number}: no_match cannot define a line range"
            )

        failure_reason = expected.get("failure_reason")
        if not isinstance(failure_reason, str) or not failure_reason.strip():
            raise EvaluationCaseError(
                f"line {line_number}: no_match requires a non-empty "
                "expected.failure_reason"
            )

        if "context" in expected:
            raise EvaluationCaseError(
                f"line {line_number}: no_match cannot define expected.context"
            )

        return

    if "failure_reason" in expected:
        raise EvaluationCaseError(
            f"line {line_number}: expected.failure_reason is only valid "
            "for no_match"
        )

    # Support either:
    #
    #   "line": 848
    #
    # or:
    #
    #   "line_min": 848,
    #   "line_max": 850
    if "line_min" not in expected and "line" in expected:
        expected["line_min"] = expected["line"]

    if "line_max" not in expected and "line_min" in expected:
        expected["line_max"] = expected["line_min"]

    if "line_min" not in expected or "line_max" not in expected:
        raise EvaluationCaseError(
            f"line {line_number}: expected requires line or line_min/line_max"
        )

    validate_integer(
        expected["line_min"],
        f"line {line_number}: expected.line_min",
        minimum=1,
    )
    validate_integer(
        expected["line_max"],
        f"line {line_number}: expected.line_max",
        minimum=1,
    )

    if expected["line_min"] > expected["line_max"]:
        raise EvaluationCaseError(
            f"line {line_number}: expected.line_min cannot exceed line_max"
        )

    validate_context_expectation(expected, line_number)


def validate_context_expectation(
        expected: dict[str, Any],
        line_number: int,
) -> None:
    """Validate optional context-boundary assertions for a matching case."""
    if "context" not in expected:
        return

    context = expected["context"]

    if not isinstance(context, dict) or not context:
        raise EvaluationCaseError(
            f"line {line_number}: expected.context must be a non-empty object"
        )

    unknown_fields = set(context) - CONTEXT_EXPECTATION_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise EvaluationCaseError(
            f"line {line_number}: unsupported expected.context fields: {names}"
        )

    for field_name in ("start_line", "end_line", "total_lines"):
        if field_name in context:
            validate_integer(
                context[field_name],
                f"line {line_number}: expected.context.{field_name}",
                minimum=1,
            )

    if "truncated" in context and not isinstance(context["truncated"], bool):
        raise EvaluationCaseError(
            f"line {line_number}: expected.context.truncated must be boolean"
        )

    start_line = context.get("start_line")
    end_line = context.get("end_line")
    total_lines = context.get("total_lines")

    if (
            isinstance(start_line, int)
            and isinstance(end_line, int)
            and start_line > end_line
    ):
        raise EvaluationCaseError(
            f"line {line_number}: expected.context.start_line cannot exceed "
            "end_line"
        )

    if (
            isinstance(end_line, int)
            and isinstance(total_lines, int)
            and end_line > total_lines
    ):
        raise EvaluationCaseError(
            f"line {line_number}: expected.context.end_line cannot exceed "
            "total_lines"
        )


def load_cases(
        cases_path: Path,
        max_cases: int | None = None,
) -> list[dict[str, Any]]:
    """Load and validate JSONL cases."""
    if not cases_path.is_file():
        raise FileNotFoundError(
            f"Evaluation cases file does not exist: {cases_path}"
        )

    if max_cases is not None and max_cases < 1:
        raise ValueError("--max-cases must be >= 1")

    cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()

    with cases_path.open("r", encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvaluationCaseError(
                    f"line {line_number}: invalid JSON: {exc.msg}"
                ) from exc

            case = validate_case(parsed, line_number)
            if case["id"] in case_ids:
                raise EvaluationCaseError(
                    f"line {line_number}: duplicate case id {case['id']!r}"
                )

            case_ids.add(case["id"])
            cases.append(case)

            if max_cases is not None and len(cases) >= max_cases:
                break

    if not cases:
        raise EvaluationCaseError(
            f"No evaluation cases found in {cases_path}"
        )

    return cases


def error_to_dict(exc: Exception) -> dict[str, str]:
    """Convert an exception to a report-safe representation."""
    message = str(exc)

    roots_to_redact: set[str] = set()

    configured_root = os.getenv("REPOSITORY_ROOT")
    if configured_root:
        configured_path = Path(configured_root).expanduser()
        roots_to_redact.add(str(configured_path))
        roots_to_redact.add(str(configured_path.resolve()))

    try:
        roots_to_redact.add(str(get_base_repository()))
    except Exception:
        pass

    for root in sorted(roots_to_redact, key=len, reverse=True):
        if root:
            message = message.replace(root, "<REPOSITORY_ROOT>")

    return {
        "type": type(exc).__name__,
        "message": message,
    }


def is_expected_match(
        matches: list[dict[str, Any]],
        expected: dict[str, Any],
) -> bool:
    """Check whether any result matches the manually labeled answer."""
    expected_repo = expected["repo"]
    expected_path = expected["path"]
    line_min = expected["line_min"]
    line_max = expected["line_max"]

    return any(
        match.get("repo") == expected_repo
        and match.get("path") == expected_path
        and line_min <= match.get("line", -1) <= line_max
        for match in matches
    )


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 3)


async def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one search and, for positive cases, a Top-1 context lookup."""
    case_started_at = time.perf_counter()
    expected = case["expected"]
    expected_outcome = expected.get("outcome", MATCH_OUTCOME)

    record: dict[str, Any] = {
        "id": case["id"],
        "category": case["category"],
        "search": case["search"],
        "expected": expected,
        "expected_result_met": False,
        "status": "pending",
        "matches": [],
        "top_match": None,
        "hit_at_1": None,
        "hit_at_5": None,
        "no_match_ok": None,
        "context_attempted": False,
        "context_skipped_reason": None,
        "context_ok": None,
        "closed_loop": None,
        "search_tool_duration_ms": None,
        "search_wall_duration_ms": None,
        "context_wall_duration_ms": None,
        "context": None,
        "context_validation": {
            "metadata_contract": None,
            "target_marker_present": None,
            "boundary_expectation_met": None,
            "failures": [],
        },
        "errors": {
            "search": None,
            "context": None,
        },
    }

    search_started_at = time.perf_counter()

    try:
        search_result = await search_code(**case["search"])
    except Exception as exc:
        record["status"] = "search_error"
        record["errors"]["search"] = error_to_dict(exc)
        record["context_skipped_reason"] = "search_error"
        record["search_wall_duration_ms"] = elapsed_ms(search_started_at)
        record["total_wall_duration_ms"] = elapsed_ms(case_started_at)
        return record
    finally:
        if record["search_wall_duration_ms"] is None:
            record["search_wall_duration_ms"] = elapsed_ms(
                search_started_at
            )

    matches = [
        model_to_dict(match)
        for match in getattr(search_result, "matches", [])
    ]

    record["matches"] = matches
    record["top_match"] = matches[0] if matches else None
    record["search_tool_duration_ms"] = getattr(
        search_result,
        "duration_ms",
        None,
    )

    if expected_outcome == NO_MATCH_OUTCOME:
        record["no_match_ok"] = not matches
        record["expected_result_met"] = record["no_match_ok"]
        record["context_skipped_reason"] = "expected_no_match"
        record["status"] = (
            "ok" if record["no_match_ok"] else "unexpected_match"
        )
        record["total_wall_duration_ms"] = elapsed_ms(case_started_at)
        return record

    record["hit_at_1"] = is_expected_match(matches[:1], expected)
    record["hit_at_5"] = is_expected_match(matches[:5], expected)

    # Use the actual Top-1 result for the second Tool call.
    # Do not scan for the gold result, otherwise the closed-loop metric
    # would be artificially inflated.
    top_match = record["top_match"]

    if top_match is None:
        record["context_ok"] = False
        record["closed_loop"] = False
        record["context_skipped_reason"] = "search_returned_no_matches"
        record["status"] = "missing_match"
        record["total_wall_duration_ms"] = elapsed_ms(case_started_at)
        return record

    record["context_attempted"] = True
    context_started_at = time.perf_counter()

    try:
        context_result = get_file_context(
            repository=top_match["repo"],
            file_path=top_match["path"],
            line_number=top_match["line"],
        )

        context_dict = model_to_dict(context_result)

        record["context"] = {
            "repository": context_dict.get("repository"),
            "file_path": context_dict.get("file_path"),
            "target_line": context_dict.get("target_line"),
            "start_line": context_dict.get("start_line"),
            "end_line": context_dict.get("end_line"),
            "total_lines": context_dict.get("total_lines"),
            "truncated": context_dict.get("truncated"),
        }

        metadata_contract = (
            context_dict.get("repository") == top_match["repo"]
            and context_dict.get("file_path") == top_match["path"]
            and context_dict.get("target_line") == top_match["line"]
        )
        record["context_validation"]["metadata_contract"] = metadata_contract

        target_marker = f">{top_match['line']:5d} |"
        target_marker_present = target_marker in context_dict.get("content", "")
        record["context_validation"]["target_marker_present"] = (
            target_marker_present
        )

        failures: list[str] = []
        if not metadata_contract:
            failures.append("metadata_contract")
        if not target_marker_present:
            failures.append("target_marker")

        expected_context = expected.get("context")
        if expected_context is not None:
            boundary_failures = [
                field_name
                for field_name, expected_value in expected_context.items()
                if context_dict.get(field_name) != expected_value
            ]
            record["context_validation"]["boundary_expectation_met"] = (
                not boundary_failures
            )
            failures.extend(
                f"expected.context.{field_name}"
                for field_name in boundary_failures
            )

        record["context_validation"]["failures"] = failures
        record["context_ok"] = not failures

    except Exception as exc:
        record["errors"]["context"] = error_to_dict(exc)
        record["context_ok"] = False
    finally:
        record["context_wall_duration_ms"] = elapsed_ms(context_started_at)

    record["closed_loop"] = bool(
        record["hit_at_1"] and record["context_ok"]
    )
    record["expected_result_met"] = record["closed_loop"]

    if record["errors"]["context"] is not None:
        record["status"] = "context_error"
    elif not record["hit_at_1"]:
        record["status"] = "top1_miss"
    elif not record["context_ok"]:
        record["status"] = "context_assertion_failed"
    else:
        record["status"] = "ok"

    record["total_wall_duration_ms"] = elapsed_ms(case_started_at)

    return record


def average(values: list[float | int | None]) -> float | None:
    valid_values = [
        float(value)
        for value in values
        if isinstance(value, (int, float))
    ]

    if not valid_values:
        return None

    return round(sum(valid_values) / len(valid_values), 3)


def rate(count: int, total: int) -> float:
    if total == 0:
        return 0.0

    return round(count / total, 4)


def metric(values: list[bool | None]) -> dict[str, int | float]:
    """Build a count, denominator, and rate for one metric population."""
    total = len(values)
    count = sum(value is True for value in values)

    return {
        "count": count,
        "total": total,
        "rate": rate(count, total),
    }


def latency_metric(values: list[float | int | None]) -> dict[str, int | float | None]:
    """Summarize raw latency measurements retained on every case record."""
    valid_values = [
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]

    if not valid_values:
        return {
            "sample_count": 0,
            "average_ms": None,
            "min_ms": None,
            "max_ms": None,
        }

    return {
        "sample_count": len(valid_values),
        "average_ms": round(sum(valid_values) / len(valid_values), 3),
        "min_ms": round(min(valid_values), 3),
        "max_ms": round(max(valid_values), 3),
    }


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate metrics without treating a correct no-hit as Hit@k."""
    total = len(records)

    positive_records = [
        record
        for record in records
        if record["expected"].get("outcome", MATCH_OUTCOME) == MATCH_OUTCOME
    ]
    no_match_records = [
        record
        for record in records
        if record["expected"].get("outcome", MATCH_OUTCOME) == NO_MATCH_OUTCOME
    ]

    search_error_count = sum(
        record["errors"]["search"] is not None
        for record in records
    )
    context_error_count = sum(
        record["errors"]["context"] is not None
        for record in records
    )

    search_tool_latencies = [
        record["search_tool_duration_ms"]
        for record in records
    ]
    search_wall_latencies = [
        record["search_wall_duration_ms"]
        for record in records
    ]
    context_wall_latencies = [
        record["context_wall_duration_ms"]
        for record in positive_records
        if record["context_attempted"]
    ]
    closed_loop_wall_latencies = [
        record["total_wall_duration_ms"]
        for record in positive_records
        if record["context_attempted"]
    ]

    return {
        "total_cases": total,
        "positive_cases": len(positive_records),
        "no_match_cases": len(no_match_records),
        "hit_at_1": metric([
            record["hit_at_1"]
            for record in positive_records
        ]),
        "hit_at_5": metric([
            record["hit_at_5"]
            for record in positive_records
        ]),
        "no_match_success": metric([
            record["no_match_ok"]
            for record in no_match_records
        ]),
        "context_success": metric([
            record["context_ok"]
            for record in positive_records
        ]),
        "closed_loop_success": metric([
            record["closed_loop"]
            for record in positive_records
        ]),
        "expected_result_success": metric([
            record["expected_result_met"]
            for record in records
        ]),
        "search_errors": search_error_count,
        "context_errors": context_error_count,
        "latency_ms": {
            "search_tool": latency_metric(search_tool_latencies),
            "search_wall": latency_metric(search_wall_latencies),
            "context_wall": latency_metric(context_wall_latencies),
            "closed_loop_wall": latency_metric(closed_loop_wall_latencies),
        },
        # Keep the original flat keys for downstream consumers of schema v1.
        "average_search_tool_duration_ms": average(search_tool_latencies),
        "average_search_wall_duration_ms": average(search_wall_latencies),
        "average_context_wall_duration_ms": average(context_wall_latencies),
        "average_closed_loop_wall_duration_ms": average(
            closed_loop_wall_latencies
        ),
        "average_total_wall_duration_ms": average([
            record["total_wall_duration_ms"]
            for record in records
        ]),
    }


def repository_revisions(cases: list[dict[str, Any]]) -> dict[str, str | None]:
    """Record checked-out revisions without exposing local repository paths."""
    repositories = sorted({
        case["expected"]["repo"]
        for case in cases
    })
    revisions: dict[str, str | None] = {}

    for repository in repositories:
        try:
            repository_root = get_repository_root(repository)
            result = subprocess.run(
                ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
            revision = result.stdout.strip()
            revisions[repository] = revision if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            revisions[repository] = None

    return revisions


def print_case_result(
        index: int,
        total: int,
        record: dict[str, Any],
) -> None:
    def display_flag(value: bool | None) -> str:
        if value is None:
            return "n/a"
        return str(int(value))

    error_names = [
        name
        for name, error in record["errors"].items()
        if error is not None
    ]

    error_suffix = ""
    if error_names:
        error_suffix = f" errors={','.join(error_names)}"

    print(
        f"[{index}/{total}] {record['id']} "
        f"status={record['status']} "
        f"hit@1={display_flag(record['hit_at_1'])} "
        f"hit@5={display_flag(record['hit_at_5'])} "
        f"no_match={display_flag(record['no_match_ok'])} "
        f"context={display_flag(record['context_ok'])} "
        f"closed_loop={display_flag(record['closed_loop'])} "
        f"search_ms={record['search_tool_duration_ms']}"
        f"{error_suffix}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Code Search MCP retrieval evaluation"
    )

    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evaluation/cases.jsonl"),
        help="JSONL evaluation cases file",
    )

    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Output JSON report path; defaults to "
            "evaluation/reports/baseline-YYYY-MM-DD.json"
        ),
    )

    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Only run the first N cases",
    )

    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()

    cases_path = resolve_project_path(args.cases)

    if args.out is None:
        date_string = datetime.now().strftime("%Y-%m-%d")
        out_path = PROJECT_ROOT / (
            f"evaluation/reports/baseline-{date_string}.json"
        )
    else:
        out_path = resolve_project_path(args.out)

    try:
        cases = load_cases(
            cases_path=cases_path,
            max_cases=args.max_cases,
        )
    except (OSError, ValueError) as exc:
        print(f"Failed to load evaluation cases: {exc}", file=sys.stderr)
        return 2

    records: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        record = await evaluate_case(case)
        records.append(record)
        print_case_result(index, len(cases), record)

    summary = build_summary(records)

    report = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution": {
            "mode": "direct_python_tool_calls",
            "search_tool": "search_code",
            "context_tool": "get_file_context",
            "context_selection": "Top-1 search result",
            "case_execution": "sequential",
            "repository_revisions": repository_revisions(cases),
            "latency_measurement": {
                "clock": "time.perf_counter",
                "search_wall": (
                    "search_code coroutine wall-clock duration for every case"
                ),
                "closed_loop_wall": (
                    "search_code plus Top-1 get_file_context wall-clock "
                    "duration for positive cases where context was attempted"
                ),
            },
        },
        "cases_file": display_path(cases_path),
        "summary": summary,
        "cases": records,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        )

    execution_error_count = (
            summary["search_errors"] + summary["context_errors"]
    )
    expected_result_failure_count = (
            summary["total_cases"]
            - summary["expected_result_success"]["count"]
    )

    print()
    print("Evaluation summary:")
    print(
        f"  Hit@1: "
        f"{summary['hit_at_1']['count']}/{summary['hit_at_1']['total']} "
        f"({summary['hit_at_1']['rate']:.2%})"
    )
    print(
        f"  Hit@5: "
        f"{summary['hit_at_5']['count']}/{summary['hit_at_5']['total']} "
        f"({summary['hit_at_5']['rate']:.2%})"
    )
    print(
        f"  Closed loop: "
        f"{summary['closed_loop_success']['count']}/"
        f"{summary['closed_loop_success']['total']} "
        f"({summary['closed_loop_success']['rate']:.2%})"
    )
    print(
        f"  No-match: "
        f"{summary['no_match_success']['count']}/"
        f"{summary['no_match_success']['total']} "
        f"({summary['no_match_success']['rate']:.2%})"
    )
    print(
        f"  Search wall latency (avg): "
        f"{summary['latency_ms']['search_wall']['average_ms']} ms"
    )
    print(
        f"  Closed-loop wall latency (avg): "
        f"{summary['latency_ms']['closed_loop_wall']['average_ms']} ms"
    )
    print(f"  Report: {display_path(out_path)}")

    return 1 if execution_error_count or expected_result_failure_count else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
