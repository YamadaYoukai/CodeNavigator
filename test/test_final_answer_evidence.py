import asyncio
from itertools import count

import pytest

from src.examples.code_understanding_agent import (
    AgentLoop,
    ContextBuilder,
    ContextState,
    Evidence,
    EvidenceKind,
    FakeModel,
    FinalAnswer,
    FinalAnswerCitation,
    FinalAnswerDecision,
    GET_FILE_CONTEXT,
    GetFileContextArguments,
    PydanticToolAdapter,
    SEARCH_CODE,
    SearchCodeArguments,
    ToolCall,
    ToolCallDecision,
    ToolResult,
    ToolRouter,
    ToolStepExecutor,
    TraceRecorder,
    TracedModelClient,
    collect_allowed_final_answer_evidence,
    validate_final_answer_evidence,
)


INSUFFICIENT_ANSWER = (
    "Insufficient verified tool evidence is available to answer the task."
)


def _search_result(*, line: int = 12) -> dict[str, object]:
    return {
        "query": "RetryPolicy",
        "duration_ms": 1,
        "matches": [
            {
                "repo": "example/repository",
                "path": "src/retry.py",
                "line": line,
                "snippet": "class RetryPolicy:",
            }
        ],
    }


def _context_result() -> dict[str, object]:
    return {
        "repository": "example/repository",
        "file_path": "src/retry.py",
        "target_line": 12,
        "start_line": 10,
        "end_line": 15,
        "total_lines": 30,
        "content": "class RetryPolicy:",
        "truncated": True,
    }


def _record_tool_pair(
    trace: TraceRecorder,
    *,
    tool_name: str,
    call_id: str,
    result: object | None,
    status: str = "success",
) -> None:
    trace.append(
        ToolCall(
            call_id=call_id,
            tool_name=tool_name,
            arguments={},
        )
    )
    if status == "success":
        trace.append(
            ToolResult(
                call_id=call_id,
                status="success",
                result=result,
            )
        )
    else:
        trace.append(
            ToolResult(
                call_id=call_id,
                status="error",
                error_type="tool_execution_error",
            )
        )


def _run_loop(
    decisions: tuple[ToolCallDecision | FinalAnswerDecision, ...],
    *,
    search_result: object | None = None,
    context_result: object | None = None,
    initial_evidence: tuple[Evidence, ...] = (),
) -> tuple[TraceRecorder, object, FakeModel]:
    trace = TraceRecorder(task_id="evidence-loop-task")
    context_builder = ContextBuilder()
    fake_model = FakeModel(decisions)
    request_numbers = count(1)
    traced_model = TracedModelClient(
        client=fake_model,
        model="fake-evidence-model",
        trace=trace,
        request_id_factory=lambda: f"request-{next(request_numbers)}",
        clock=lambda: 0.0,
    )

    def search_code(**_: object) -> object:
        if search_result is None:
            raise AssertionError("search_code was not expected")
        return search_result

    def get_file_context(**_: object) -> object:
        if context_result is None:
            raise AssertionError("get_file_context was not expected")
        return context_result

    router = ToolRouter(
        trace=trace,
        tools={
            SEARCH_CODE: PydanticToolAdapter(SearchCodeArguments, search_code),
            GET_FILE_CONTEXT: PydanticToolAdapter(
                GetFileContextArguments,
                get_file_context,
            ),
        },
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
        system_instruction="Answer only from supplied code evidence.",
        current_task="Locate RetryPolicy.",
        evidence=initial_evidence,
        evidence_item_budget=4,
        remaining_tool_calls=6,
    )
    outcome = asyncio.run(loop.run(state))
    return trace, outcome, fake_model


def _assert_insufficient(trace: TraceRecorder, outcome: object) -> None:
    assert hasattr(outcome, "final_answer")
    final = outcome.final_answer
    assert final.answer == INSUFFICIENT_ANSWER
    assert final.termination_reason == "insufficient_evidence"
    assert final.evidence == []
    assert final.uncertainties == []
    assert final.next_queries == []
    assert trace.events[-1] == final
    assert len([event for event in trace.events if isinstance(event, FinalAnswer)]) == 1
    assert {event.task_id for event in trace.events} == {trace.task_id}
    assert [event.sequence for event in trace.events] == list(
        range(1, len(trace.events) + 1)
    )
    assert trace.is_finalized is True


def test_should_collect_only_exact_search_hits_and_file_context_range() -> None:
    trace = TraceRecorder(task_id="allowed-evidence")
    _record_tool_pair(
        trace,
        tool_name=SEARCH_CODE,
        call_id="search",
        result=_search_result(),
    )
    _record_tool_pair(
        trace,
        tool_name=GET_FILE_CONTEXT,
        call_id="context",
        result=_context_result(),
    )

    allowed = collect_allowed_final_answer_evidence(
        trace.events,
        task_id=trace.task_id,
    )

    assert allowed == {
        "example/repository/src/retry.py:10",
        "example/repository/src/retry.py:11",
        "example/repository/src/retry.py:12",
        "example/repository/src/retry.py:13",
        "example/repository/src/retry.py:14",
        "example/repository/src/retry.py:15",
    }


@pytest.mark.parametrize(
    ("tool_name", "arguments", "tool_result", "citation"),
    [
        pytest.param(
            SEARCH_CODE,
            {"query": "RetryPolicy"},
            _search_result(),
            FinalAnswerCitation(
                repo="example/repository",
                path="src/retry.py",
                line=12,
            ),
            id="search-hit",
        ),
        pytest.param(
            GET_FILE_CONTEXT,
            {
                "repository": "example/repository",
                "file_path": "src/retry.py",
                "line_number": 12,
            },
            _context_result(),
            FinalAnswerCitation(
                repo="example/repository",
                path="src/retry.py",
                line=14,
            ),
            id="file-context-range",
        ),
    ],
)
def test_should_complete_with_exact_current_task_tool_evidence(
    tool_name: str,
    arguments: dict[str, object],
    tool_result: object,
    citation: FinalAnswerCitation,
) -> None:
    decisions = (
        ToolCallDecision(
            call_id="call-1",
            tool_name=tool_name,
            arguments=arguments,
        ),
        FinalAnswerDecision(
            answer="RetryPolicy is declared in src/retry.py.",
            evidence=(citation,),
            uncertainties=("Callers were not inspected.",),
            next_queries=("RetryPolicy callers",),
        ),
    )

    trace, outcome, fake_model = _run_loop(
        decisions,
        search_result=tool_result if tool_name == SEARCH_CODE else None,
        context_result=tool_result if tool_name == GET_FILE_CONTEXT else None,
    )

    assert outcome.final_answer.termination_reason == "completed"
    assert outcome.final_answer.answer == decisions[-1].answer
    assert outcome.final_answer.evidence == [citation.to_canonical()]
    assert outcome.final_answer.uncertainties == ["Callers were not inspected."]
    assert outcome.final_answer.next_queries == ["RetryPolicy callers"]
    assert trace.events[-1] == outcome.final_answer
    assert fake_model.remaining_decisions == 0


def test_should_reject_empty_evidence_and_not_preserve_model_answer() -> None:
    decisions = (
        FinalAnswerDecision(
            answer="Unsupported answer must not escape.",
            evidence=(),
            uncertainties=("unsupported uncertainty",),
            next_queries=("unsupported next query",),
        ),
    )

    trace, outcome, _ = _run_loop(decisions)

    _assert_insufficient(trace, outcome)
    assert "Unsupported answer" not in outcome.final_answer.answer
    assert outcome.tool_calls_used == 0


def test_should_not_trust_caller_preloaded_fact_or_history() -> None:
    citation = FinalAnswerCitation(
        repo="example/repository",
        path="src/retry.py",
        line=12,
    )
    canonical_citation = citation.to_canonical()
    initial_evidence = (
        Evidence(kind=EvidenceKind.FACT, source="caller", content=canonical_citation),
        Evidence(
            kind=EvidenceKind.HISTORY,
            source="caller",
            content=canonical_citation,
        ),
    )

    trace, outcome, _ = _run_loop(
        (
            FinalAnswerDecision(
                answer="Caller context is not a current Tool fact.",
                evidence=(citation,),
            ),
        ),
        initial_evidence=initial_evidence,
    )

    _assert_insufficient(trace, outcome)


@pytest.mark.parametrize(
    "citation",
    [
        FinalAnswerCitation(repo="forged/repository", path="src/retry.py", line=12),
        FinalAnswerCitation(
            repo="example/repository",
            path="src/forged.py",
            line=12,
        ),
        FinalAnswerCitation(
            repo="example/repository",
            path="src/retry.py",
            line=11,
        ),
        FinalAnswerCitation(
            repo="example/repository",
            path="src/retry.py",
            line=13,
        ),
    ],
)
def test_should_fail_closed_for_structurally_valid_unsupported_citation(
    citation: FinalAnswerCitation,
) -> None:
    decisions = (
        ToolCallDecision(
            call_id="search",
            tool_name=SEARCH_CODE,
            arguments={"query": "RetryPolicy"},
        ),
        FinalAnswerDecision(answer="Unsupported.", evidence=(citation,)),
    )

    trace, outcome, _ = _run_loop(decisions, search_result=_search_result())

    _assert_insufficient(trace, outcome)


def test_should_reject_entire_answer_when_one_citation_is_forged() -> None:
    decisions = (
        ToolCallDecision(
            call_id="search",
            tool_name=SEARCH_CODE,
            arguments={"query": "RetryPolicy"},
        ),
        FinalAnswerDecision(
            answer="Mixed evidence must fail closed.",
            evidence=(
                FinalAnswerCitation(
                    repo="example/repository",
                    path="src/retry.py",
                    line=12,
                ),
                FinalAnswerCitation(
                    repo="example/repository",
                    path="src/retry.py",
                    line=13,
                ),
            ),
        ),
    )

    trace, outcome, _ = _run_loop(decisions, search_result=_search_result())

    _assert_insufficient(trace, outcome)
    assert outcome.final_answer.evidence == []


def test_should_ignore_failed_tool_result() -> None:
    trace = TraceRecorder(task_id="failed-tool")
    _record_tool_pair(
        trace,
        tool_name=SEARCH_CODE,
        call_id="search",
        result=None,
        status="error",
    )

    validation = validate_final_answer_evidence(
        ("example/repository/src/retry.py:12",),
        events=trace.events,
        task_id=trace.task_id,
    )

    assert validation.is_valid is False
    assert validation.allowed_evidence == frozenset()


def test_should_ignore_other_task_and_incomplete_correlation() -> None:
    other_trace = TraceRecorder(task_id="other-task")
    _record_tool_pair(
        other_trace,
        tool_name=SEARCH_CODE,
        call_id="search",
        result=_search_result(),
    )
    incomplete_trace = TraceRecorder(task_id="current-task")
    incomplete_trace.append(
        ToolCall(call_id="search", tool_name=SEARCH_CODE, arguments={})
    )

    other_task_allowed = collect_allowed_final_answer_evidence(
        other_trace.events,
        task_id="current-task",
    )
    incomplete_allowed = collect_allowed_final_answer_evidence(
        incomplete_trace.events,
        task_id=incomplete_trace.task_id,
    )

    assert other_task_allowed == frozenset()
    assert incomplete_allowed == frozenset()


def test_should_reject_malformed_success_results() -> None:
    trace = TraceRecorder(task_id="malformed-results")
    _record_tool_pair(
        trace,
        tool_name=SEARCH_CODE,
        call_id="search",
        result={
            "matches": [
                {"repo": "click", "path": "src/core.py", "line": 0},
                {"repo": "click", "path": "../core.py", "line": 12},
                {"repo": "click", "path": "src/core.py", "line": "12"},
            ]
        },
    )
    _record_tool_pair(
        trace,
        tool_name=GET_FILE_CONTEXT,
        call_id="context",
        result={
            "repository": "click",
            "file_path": "src/core.py",
            "start_line": 15,
            "end_line": 10,
        },
    )

    assert collect_allowed_final_answer_evidence(
        trace.events,
        task_id=trace.task_id,
    ) == frozenset()
