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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_base_repository
from src.server import get_file_context, search_code


SEARCH_FIELDS = {
    "query",
    "repo",
    "lang",
    "path",
    "limit",
    "literal",
}


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

    expected = case.get("expected")
    if not isinstance(expected, dict):
        raise EvaluationCaseError(
            f"line {line_number}: 'expected' must be an object"
        )

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

    return case


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
    """Run one search and one context lookup using the Top-1 result."""
    case_started_at = time.perf_counter()

    record: dict[str, Any] = {
        "id": case["id"],
        "category": case["category"],
        "search": case["search"],
        "expected": case["expected"],
        "status": "pending",
        "matches": [],
        "top_match": None,
        "hit_at_1": False,
        "hit_at_5": False,
        "context_ok": False,
        "closed_loop": False,
        "search_tool_duration_ms": None,
        "search_wall_duration_ms": None,
        "context_wall_duration_ms": None,
        "context": None,
        "errors": {
            "search": None,
            "context": None,
        },
    }

    search_result = None

    search_started_at = time.perf_counter()

    try:
        search_result = await search_code(**case["search"])
    except Exception as exc:
        record["status"] = "search_error"
        record["errors"]["search"] = error_to_dict(exc)
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

    record["status"] = "ok"
    record["matches"] = matches
    record["top_match"] = matches[0] if matches else None
    record["search_tool_duration_ms"] = getattr(
        search_result,
        "duration_ms",
        None,
    )

    expected = case["expected"]

    record["hit_at_1"] = is_expected_match(matches[:1], expected)
    record["hit_at_5"] = is_expected_match(matches[:5], expected)

    # Use the actual Top-1 result for the second Tool call.
    # Do not scan for the gold result, otherwise the closed-loop metric
    # would be artificially inflated.
    top_match = record["top_match"]

    if top_match is not None:
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

            contract_is_valid = (
                    context_dict.get("repository") == top_match["repo"]
                    and context_dict.get("file_path") == top_match["path"]
                    and context_dict.get("target_line") == top_match["line"]
            )

            if not contract_is_valid:
                raise RuntimeError(
                    "get_file_context returned metadata inconsistent "
                    "with the Top-1 search result"
                )

            record["context_ok"] = True

        except Exception as exc:
            record["errors"]["context"] = error_to_dict(exc)
        finally:
            record["context_wall_duration_ms"] = elapsed_ms(
                context_started_at
            )

    record["closed_loop"] = bool(
        record["hit_at_1"] and record["context_ok"]
    )
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


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate evaluation metrics."""
    total = len(records)

    hit_at_1_count = sum(
        bool(record["hit_at_1"])
        for record in records
    )
    hit_at_5_count = sum(
        bool(record["hit_at_5"])
        for record in records
    )
    context_ok_count = sum(
        bool(record["context_ok"])
        for record in records
    )
    closed_loop_count = sum(
        bool(record["closed_loop"])
        for record in records
    )

    search_error_count = sum(
        record["errors"]["search"] is not None
        for record in records
    )
    context_error_count = sum(
        record["errors"]["context"] is not None
        for record in records
    )

    return {
        "total_cases": total,
        "hit_at_1": {
            "count": hit_at_1_count,
            "rate": rate(hit_at_1_count, total),
        },
        "hit_at_5": {
            "count": hit_at_5_count,
            "rate": rate(hit_at_5_count, total),
        },
        "context_success": {
            "count": context_ok_count,
            "rate": rate(context_ok_count, total),
        },
        "closed_loop_success": {
            "count": closed_loop_count,
            "rate": rate(closed_loop_count, total),
        },
        "search_errors": search_error_count,
        "context_errors": context_error_count,
        "average_search_tool_duration_ms": average(
            [
                record["search_tool_duration_ms"]
                for record in records
            ]
        ),
        "average_search_wall_duration_ms": average(
            [
                record["search_wall_duration_ms"]
                for record in records
            ]
        ),
        "average_context_wall_duration_ms": average(
            [
                record["context_wall_duration_ms"]
                for record in records
            ]
        ),
        "average_total_wall_duration_ms": average(
            [
                record["total_wall_duration_ms"]
                for record in records
            ]
        ),
    }


def print_case_result(
        index: int,
        total: int,
        record: dict[str, Any],
) -> None:
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
        f"hit@1={int(record['hit_at_1'])} "
        f"hit@5={int(record['hit_at_5'])} "
        f"context={int(record['context_ok'])} "
        f"closed_loop={int(record['closed_loop'])} "
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
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution": {
            "mode": "direct_python_tool_calls",
            "search_tool": "search_code",
            "context_tool": "get_file_context",
            "context_selection": "Top-1 search result",
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

    print()
    print("Evaluation summary:")
    print(
        f"  Hit@1: "
        f"{summary['hit_at_1']['count']}/{summary['total_cases']} "
        f"({summary['hit_at_1']['rate']:.2%})"
    )
    print(
        f"  Hit@5: "
        f"{summary['hit_at_5']['count']}/{summary['total_cases']} "
        f"({summary['hit_at_5']['rate']:.2%})"
    )
    print(
        f"  Closed loop: "
        f"{summary['closed_loop_success']['count']}/"
        f"{summary['total_cases']} "
        f"({summary['closed_loop_success']['rate']:.2%})"
    )
    print(
        f"  Average search tool latency: "
        f"{summary['average_search_tool_duration_ms']} ms"
    )
    print(f"  Report: {display_path(out_path)}")

    return 1 if execution_error_count else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))