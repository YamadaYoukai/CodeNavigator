import asyncio
import json

import pytest

import evaluation.run_agent_eval as agent_eval
from evaluation.run_agent_eval import (
    DEFAULT_CASES_PATH,
    FROZEN_DATASET_SHA256,
    PINNED_CLICK_REVISION,
    AgentEvalDataError,
    analyze_trace,
    assert_report_is_sanitized,
    build_agent_summary,
    evaluate_agent_case,
    load_agent_cases,
    parse_citation,
    preflight_environment,
    render_markdown_report,
    validate_frozen_dataset,
)
from src.examples.code_understanding_agent import (
    FakeModel,
    FinalAnswer,
    FinalAnswerCitation,
    FinalAnswerDecision,
    SEARCH_CODE,
    ToolCallDecision,
    TraceRecorder,
)


def _search_result(*, matches: list[dict[str, object]]) -> dict[str, object]:
    return {"query": "echo", "duration_ms": 1, "matches": matches}


def _unexpected_context(**_: object) -> object:
    raise AssertionError("get_file_context was not expected")


def test_frozen_agent_dataset_validates_with_recorded_hash_and_distribution() -> None:
    cases, digest = validate_frozen_dataset(DEFAULT_CASES_PATH)

    assert len(cases) == 10
    assert digest == FROZEN_DATASET_SHA256
    assert {case.click_revision for case in cases} == {PINNED_CLICK_REVISION}
    assert [case.expected.result for case in cases].count("answerable") == 8
    assert [
        case.expected.result for case in cases
    ].count("insufficient_evidence") == 2


def test_agent_dataset_rejects_duplicate_ids(tmp_path) -> None:
    raw_cases = DEFAULT_CASES_PATH.read_text(encoding="utf-8").splitlines()
    first = json.loads(raw_cases[0])
    second = json.loads(raw_cases[1])
    second["id"] = first["id"]
    raw_cases[1] = json.dumps(second)
    cases_path = tmp_path / "duplicates.jsonl"
    cases_path.write_text("\n".join(raw_cases) + "\n", encoding="utf-8")

    with pytest.raises(AgentEvalDataError, match="ids must be unique"):
        load_agent_cases(cases_path)


@pytest.mark.parametrize(
    ("citation", "expected"),
    [
        (
            "click/src/click/utils.py:234",
            {
                "ok": True,
                "repo": "click",
                "path": "src/click/utils.py",
                "line": 234,
            },
        ),
        ("click/src/click/utils.py:0", {"ok": False, "error": "invalid_positive_line"}),
        ("click:234", {"ok": False, "error": "missing_repository_path_separator"}),
        ("click/../utils.py:234", {"ok": False, "error": "invalid_repository_or_path"}),
    ],
)
def test_parse_citation_reports_stable_outcome(
    citation: str,
    expected: dict[str, object],
) -> None:
    assert parse_citation(citation) == expected


def test_trace_analysis_requires_one_last_terminal() -> None:
    complete = TraceRecorder(task_id="complete")
    complete.finalize(
        FinalAnswer(
            answer="done",
            evidence=[],
            termination_reason="insufficient_evidence",
        )
    )
    incomplete = TraceRecorder(task_id="incomplete")

    assert analyze_trace(complete)["ok"] is True
    assert analyze_trace(incomplete)["ok"] is False
    assert analyze_trace(incomplete)["unique_terminal"] is False


def test_preflight_checks_revision_name_probes_and_retrieval_data(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_git_output(_repository, *arguments: str) -> str:
        if arguments == ("rev-parse", "HEAD"):
            return PINNED_CLICK_REVISION
        if arguments == ("config", "--get", "zoekt.name"):
            return "click"
        raise AssertionError(arguments)

    async def search_code(**arguments: object) -> dict[str, object]:
        query = arguments["query"]
        if query == r"def echo\(":
            matches = [
                {
                    "repo": "click",
                    "path": "src/click/utils.py",
                    "line": 234,
                }
            ]
        elif query == r"def get_missing_message\(":
            matches = [
                {
                    "repo": "click",
                    "path": "src/click/types.py",
                    "line": 358,
                }
            ]
        else:
            matches = []
        return {"matches": matches}

    monkeypatch.setattr(agent_eval, "_git_output", fake_git_output)
    monkeypatch.setattr(
        agent_eval,
        "load_retrieval_cases",
        lambda _path: [{} for _ in range(18)],
    )

    result = asyncio.run(
        preflight_environment(
            repository=tmp_path,
            declared_index_revision=PINNED_CLICK_REVISION,
            search_code=search_code,
        )
    )

    assert result["index_revision_check"]["status"] == "passed"
    assert result["repository_zoekt_name"] == "click"
    assert all(probe["passed"] for probe in result["pinned_content_probes"])
    assert result["retrieval_dataset_validation"] == {
        "status": "passed",
        "case_count": 18,
    }


def test_evaluate_answerable_case_records_valid_provenance_and_gold() -> None:
    case = validate_frozen_dataset(DEFAULT_CASES_PATH)[0][0]
    model = FakeModel(
        (
            ToolCallDecision(
                call_id="search-echo",
                tool_name=SEARCH_CODE,
                arguments={
                    "query": "def echo(",
                    "repo": "click",
                    "lang": "python",
                    "path": r"src/click/utils\.py",
                    "limit": 10,
                    "literal": True,
                },
            ),
            FinalAnswerDecision(
                answer="echo is implemented in Click's utils module.",
                evidence=(
                    FinalAnswerCitation(
                        repo="click",
                        path="src/click/utils.py",
                        line=234,
                    ),
                ),
            ),
        )
    )

    record = asyncio.run(
        evaluate_agent_case(
            case,
            model=model,
            model_name="fake-agent-eval",
            search_code=lambda **_: _search_result(
                matches=[
                    {
                        "repo": "click",
                        "path": "src/click/utils.py",
                        "line": 234,
                        "snippet": "def echo(",
                    }
                ]
            ),
            get_file_context=_unexpected_context,
            task_id="answerable-case",
        )
    )

    assert record["success"] is True
    assert record["failure_category"] is None
    assert record["final_answer"]["termination_reason"] == "completed"
    assert record["final_answer"]["evidence"] == [
        "click/src/click/utils.py:234"
    ]
    assert record["submitted_final_decision"]["evidence"] == [
        "click/src/click/utils.py:234"
    ]
    assert record["tool_attempts"]["count"] == 1
    assert record["tool_attempts"]["names"] == [SEARCH_CODE]
    assert record["trace_integrity"]["ok"] is True
    assert record["citations"] == [
        {
            "citation": "click/src/click/utils.py:234",
            "parse": {
                "ok": True,
                "repo": "click",
                "path": "src/click/utils.py",
                "line": 234,
            },
            "from_current_successful_tool_fact": True,
            "gold_match": True,
        }
    ]
    assert record["latency_ms"]["tool"]["count"] == 1


def test_evaluate_negative_case_counts_only_clean_insufficient_terminal() -> None:
    case = validate_frozen_dataset(DEFAULT_CASES_PATH)[0][8]
    model = FakeModel(
        (
            ToolCallDecision(
                call_id="search-missing",
                tool_name=SEARCH_CODE,
                arguments={
                    "query": "ClickOptionRegistry",
                    "repo": "click",
                    "lang": "python",
                    "path": None,
                    "limit": 10,
                    "literal": True,
                },
            ),
            FinalAnswerDecision(
                answer="No successful source evidence was found.",
                evidence=(),
            ),
        )
    )

    record = asyncio.run(
        evaluate_agent_case(
            case,
            model=model,
            model_name="fake-agent-eval",
            search_code=lambda **_: _search_result(matches=[]),
            get_file_context=_unexpected_context,
            task_id="negative-case",
        )
    )

    assert record["success"] is True
    assert record["failure_category"] is None
    assert record["final_answer"]["termination_reason"] == "insufficient_evidence"
    assert record["final_answer"]["evidence"] == []
    assert record["submitted_final_decision"]["evidence"] == []
    assert record["retrieval_no_match_observed"] is True


def test_rejected_model_citation_remains_in_metric_denominator() -> None:
    case = validate_frozen_dataset(DEFAULT_CASES_PATH)[0][0]
    model = FakeModel(
        (
            ToolCallDecision(
                call_id="search-echo",
                tool_name=SEARCH_CODE,
                arguments={"query": "echo", "repo": "click"},
            ),
            FinalAnswerDecision(
                answer="unsupported",
                evidence=(
                    FinalAnswerCitation(
                        repo="click",
                        path="src/click/forged.py",
                        line=999,
                    ),
                ),
            ),
        )
    )

    record = asyncio.run(
        evaluate_agent_case(
            case,
            model=model,
            model_name="fake-agent-eval",
            search_code=lambda **_: _search_result(
                matches=[
                    {
                        "repo": "click",
                        "path": "src/click/utils.py",
                        "line": 234,
                        "snippet": "def echo(",
                    }
                ]
            ),
            get_file_context=_unexpected_context,
            task_id="forged-case",
        )
    )
    summary = build_agent_summary([record])

    assert record["success"] is False
    assert record["failure_category"] == "model_judgment_failure"
    assert record["final_answer"]["termination_reason"] == "insufficient_evidence"
    assert record["final_answer"]["evidence"] == []
    assert summary["citation_validity"] == {
        "numerator": 0,
        "denominator": 1,
        "rate": 0.0,
    }


def test_summary_is_recomputable_from_positive_and_negative_records() -> None:
    cases = validate_frozen_dataset(DEFAULT_CASES_PATH)[0]
    positive_model = FakeModel(
        (
            ToolCallDecision(
                call_id="search-echo",
                tool_name=SEARCH_CODE,
                arguments={"query": "echo", "repo": "click"},
            ),
            FinalAnswerDecision(
                answer="located",
                evidence=(
                    FinalAnswerCitation(
                        repo="click",
                        path="src/click/utils.py",
                        line=234,
                    ),
                ),
            ),
        )
    )
    negative_model = FakeModel(
        (FinalAnswerDecision(answer="insufficient", evidence=()),)
    )
    positive = asyncio.run(
        evaluate_agent_case(
            cases[0],
            model=positive_model,
            model_name="fake",
            search_code=lambda **_: _search_result(
                matches=[
                    {
                        "repo": "click",
                        "path": "src/click/utils.py",
                        "line": 234,
                        "snippet": "def echo(",
                    }
                ]
            ),
            get_file_context=_unexpected_context,
            task_id="summary-positive",
        )
    )
    negative = asyncio.run(
        evaluate_agent_case(
            cases[8],
            model=negative_model,
            model_name="fake",
            search_code=lambda **_: _search_result(matches=[]),
            get_file_context=_unexpected_context,
            task_id="summary-negative",
        )
    )

    summary = build_agent_summary([positive, negative])

    assert summary["task_success_rate"] == {
        "numerator": 2,
        "denominator": 2,
        "rate": 1.0,
    }
    assert summary["termination_reason_distribution"] == {
        "completed": 1,
        "insufficient_evidence": 1,
    }
    assert summary["citation_validity"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
    }
    assert summary["trace_integrity"]["numerator"] == 2
    assert summary["tool_attempts"]["maximum"] == 1


def test_report_sanitization_rejects_values_and_forbidden_keys() -> None:
    assert assert_report_is_sanitized(
        {"safe": "public"},
        sensitive_values=("secret-token",),
    ) == {
        "configured_sensitive_values_absent": True,
        "transport_and_local_path_fields_absent": True,
    }

    with pytest.raises(RuntimeError, match="report_sanitization_failed"):
        assert_report_is_sanitized(
            {"safe": "secret-token"},
            sensitive_values=("secret-token",),
        )
    with pytest.raises(RuntimeError, match="report_sanitization_failed"):
        assert_report_is_sanitized(
            {"base_url": "redacted"},
            sensitive_values=(),
        )


def test_markdown_renderer_includes_honest_index_boundary() -> None:
    report = {
        "dataset": {
            "sha256": FROZEN_DATASET_SHA256,
            "case_count": 10,
            "click_revision": PINNED_CLICK_REVISION,
        },
        "summary": {
            "task_success_rate": {"numerator": 10, "denominator": 10, "rate": 1.0},
            "citation_validity": {"numerator": 8, "denominator": 8, "rate": 1.0},
            "trace_integrity": {"numerator": 10, "denominator": 10, "rate": 1.0},
            "tool_attempts": {"average": 1.0, "maximum": 1, "distribution": {"1": 10}},
            "termination_reason_distribution": {"completed": 8, "insufficient_evidence": 2},
            "latency_ms": {"model_calls": {}, "tools": {}, "tasks": {}},
            "failures": {},
        },
        "execution": {
            "preflight": {
                "checkout_revision": PINNED_CLICK_REVISION,
                "pinned_content_probes": [{"id": "a", "passed": True}],
            }
        },
        "verdict": {"m3_exit_criteria_met": True},
        "cases": [],
    }

    rendered = render_markdown_report(report)

    assert "M3 exit criteria are **met**" in rendered
    assert "server does not expose" in rendered
    assert FROZEN_DATASET_SHA256 in rendered
