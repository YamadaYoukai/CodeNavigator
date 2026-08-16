import hashlib
import json
from pathlib import Path

from evaluation.run_agent_eval import assert_report_is_sanitized, parse_citation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    PROJECT_ROOT
    / "evaluation/fixtures/agent-eval-range-citation-failures-2026-08-16.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _allowed_citations(case: dict[str, object]) -> set[str]:
    trace = case["trace"]
    assert isinstance(trace, dict)
    events = trace["events"]
    assert isinstance(events, list)
    allowed: set[str] = set()

    for index, event in enumerate(events[:-1]):
        assert isinstance(event, dict)
        if event.get("event_type") != "tool_call":
            continue

        result = events[index + 1]
        assert isinstance(result, dict)
        if (
            result.get("event_type") != "tool_result"
            or result.get("status") != "success"
            or result.get("call_id") != event.get("call_id")
        ):
            continue

        payload = result.get("result")
        if not isinstance(payload, dict):
            continue
        if event.get("tool_name") == "search_code":
            for match in payload.get("matches", []):
                assert isinstance(match, dict)
                allowed.add(
                    f"{match['repo']}/{match['path']}:{match['line']}"
                )
        elif event.get("tool_name") == "get_file_context":
            repository = payload["repository"]
            file_path = payload["file_path"]
            for line in range(payload["start_line"], payload["end_line"] + 1):
                allowed.add(f"{repository}/{file_path}:{line}")

    return allowed


def _matches_gold(citation: dict[str, object], case: dict[str, object]) -> bool:
    gold = case["gold"]
    assert isinstance(gold, dict)
    locations = gold["locations"]
    assert isinstance(locations, list)
    return any(
        citation["repo"] == location["repo"]
        and citation["path"] == location["path"]
        and location["line_min"] <= citation["line"] <= location["line_max"]
        for location in locations
    )


def test_range_citation_failure_fixture_matches_frozen_source_report() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    source_path = PROJECT_ROOT / fixture["source_report"]
    source = json.loads(source_path.read_text(encoding="utf-8"))

    assert_report_is_sanitized(fixture, sensitive_values=())
    assert _sha256(source_path) == fixture["source_report_sha256"]
    assert fixture["dataset_sha256"] == source["dataset"]["sha256"]

    selected = [
        case
        for case in source["cases"]
        if case["failure_category"] == "model_judgment_failure"
        and any(
            citation["parse"].get("error") == "invalid_positive_line"
            for citation in case["citations"]
        )
    ]
    frozen = fixture["cases"]

    assert fixture["case_count"] == len(frozen) == len(selected) == 6
    assert [case["case_id"] for case in frozen] == [
        case["case_id"] for case in selected
    ]

    source_by_id = {case["case_id"]: case for case in selected}
    for frozen_case in frozen:
        source_case = source_by_id[frozen_case["case_id"]]

        assert frozen_case["model_output"] == source_case["submitted_final_decision"]
        assert frozen_case["failure"] == {
            "category": source_case["failure_category"],
            "mechanism": "range_citation_not_exact_tool_fact",
            "parse_error": "invalid_positive_line",
            "runtime_termination": source_case["final_answer"]["termination_reason"],
        }

        expected = frozen_case["expected_single_line_citations"]
        assert expected
        allowed = _allowed_citations(source_case)
        canonical = {
            f"{citation['repo']}/{citation['path']}:{citation['line']}"
            for citation in expected
        }
        assert all(parse_citation(citation)["ok"] for citation in canonical)
        assert canonical <= allowed
        assert any(_matches_gold(citation, source_case) for citation in expected)
