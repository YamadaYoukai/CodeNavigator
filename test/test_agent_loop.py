import asyncio
from collections import Counter

from src.examples.code_understanding_agent import (
    AgentLoop,
    ContextBuilder,
    ContextState,
    Evidence,
    EvidenceKind,
    FakeModel,
    FinalAnswer,
    FinalAnswerDecision,
    GET_FILE_CONTEXT,
    GetFileContextArguments,
    MAX_TOOL_CALLS_PER_TASK,
    ModelRequest,
    ModelResult,
    PydanticToolAdapter,
    SEARCH_CODE,
    SearchCodeArguments,
    Step,
    ToolCall,
    ToolCallDecision,
    ToolResult,
    ToolRouter,
    ToolStepExecutor,
    TraceRecorder,
    TracedModelClient,
)


def test_should_run_two_tool_success_path_into_one_terminal_answer() -> None:
    trace = TraceRecorder(task_id="agent-loop-success")
    context_builder = ContextBuilder()
    search_arguments = {
        "query": "RetryPolicy",
        "repo": "example/repository",
        "limit": 3,
    }
    file_arguments = {
        "repository": "example/repository",
        "file_path": "src/retry.py",
        "line_number": 12,
        "lines_before": 2,
        "lines_after": 3,
    }
    scripted_decisions = (
        ToolCallDecision(
            call_id="call-search",
            tool_name=SEARCH_CODE,
            arguments=search_arguments,
        ),
        ToolCallDecision(
            call_id="call-file",
            tool_name=GET_FILE_CONTEXT,
            arguments=file_arguments,
        ),
        FinalAnswerDecision(
            answer="RetryPolicy is declared in src/retry.py.",
            evidence=("example/repository/src/retry.py:12",),
            uncertainties=(),
            next_queries=("RetryPolicy callers",),
        ),
    )
    original_script = tuple(
        decision.model_dump(mode="json") for decision in scripted_decisions
    )
    original_search_arguments = dict(search_arguments)
    original_file_arguments = dict(file_arguments)
    fake_model = FakeModel(scripted_decisions)
    request_ids = iter(("request-search", "request-file", "request-final"))
    traced_model = TracedModelClient(
        client=fake_model,
        model="fake-agent-model",
        trace=trace,
        request_id_factory=lambda: next(request_ids),
        clock=lambda: 0.0,
    )

    def search_code(**arguments: object) -> dict[str, object]:
        assert arguments["query"] == "RetryPolicy"
        return {
            "matches": [
                {
                    "repository": "example/repository",
                    "file_path": "src/retry.py",
                    "line_number": 12,
                }
            ]
        }

    def get_file_context(**arguments: object) -> dict[str, object]:
        assert arguments["file_path"] == "src/retry.py"
        return {
            "repository": "example/repository",
            "file_path": "src/retry.py",
            "start_line": 10,
            "end_line": 15,
            "content": "class RetryPolicy:",
        }

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
    initial_state = ContextState(
        system_instruction="Answer only from supplied code evidence.",
        current_task="Locate RetryPolicy and explain where it is declared.",
        evidence=(
            Evidence(
                kind=EvidenceKind.HISTORY,
                source="user",
                content="Start with a repository search.",
            ),
        ),
        evidence_item_budget=3,
        # Deliberately exceeds the harness cap to prove the loop owns the hard
        # limit rather than trusting this caller-controlled value.
        remaining_tool_calls=99,
    )
    original_state = initial_state.model_dump(mode="json")

    outcome = asyncio.run(loop.run(initial_state))

    assert outcome.tool_calls_used == 2
    assert outcome.tool_calls_used <= MAX_TOOL_CALLS_PER_TASK
    assert outcome.final_state.remaining_tool_calls == 4
    assert [item.remaining_tool_calls for item in fake_model.model_inputs] == [
        6,
        5,
        4,
    ]
    assert len(fake_model.model_inputs) == 3
    assert "call-search" in fake_model.model_inputs[1].evidence[0].source
    assert "src/retry.py" in fake_model.model_inputs[1].evidence[0].content
    assert "call-file" in fake_model.model_inputs[2].evidence[0].source
    assert "class RetryPolicy:" in fake_model.model_inputs[2].evidence[0].content

    events = trace.events
    assert [event.event_type for event in events] == [
        "session",
        "step",
        "model_request",
        "model_result",
        "tool_call",
        "tool_result",
        "step",
        "model_request",
        "model_result",
        "tool_call",
        "tool_result",
        "step",
        "model_request",
        "model_result",
        "final_answer",
    ]
    assert [event.step_number for event in events if isinstance(event, Step)] == [
        1,
        2,
        3,
    ]
    assert {event.task_id for event in events} == {"agent-loop-success"}
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))

    model_request_ids = [
        event.request_id for event in events if isinstance(event, ModelRequest)
    ]
    model_result_ids = [
        event.request_id for event in events if isinstance(event, ModelResult)
    ]
    assert model_request_ids == model_result_ids == [
        "request-search",
        "request-file",
        "request-final",
    ]
    tool_call_ids = [
        event.call_id for event in events if isinstance(event, ToolCall)
    ]
    tool_result_ids = [
        event.call_id for event in events if isinstance(event, ToolResult)
    ]
    assert tool_call_ids == tool_result_ids == ["call-search", "call-file"]
    assert Counter((*tool_call_ids, *tool_result_ids)) == {
        "call-search": 2,
        "call-file": 2,
    }

    final_events = [event for event in events if isinstance(event, FinalAnswer)]
    assert len(final_events) == 1
    assert final_events[0] == outcome.final_answer
    assert final_events[0].termination_reason == "completed"
    assert events[-1] == final_events[0]
    assert trace.is_finalized is True
    assert fake_model.remaining_decisions == 0

    assert initial_state.model_dump(mode="json") == original_state
    assert tuple(
        decision.model_dump(mode="json") for decision in scripted_decisions
    ) == original_script
    assert search_arguments == original_search_arguments
    assert file_arguments == original_file_arguments
