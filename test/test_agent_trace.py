import pytest

from src.examples.code_understanding_agent import (
    FinalAnswer,
    Session,
    Step,
    ToolCall,
    ToolResult,
    TraceRecorder,
)


def build_completed_trace() -> TraceRecorder:
    trace = TraceRecorder(task_id="demo-task-001")
    trace.append(
        Session(
            user_task="Locate the retry policy for a failed request.",
            available_tools=["search_code", "get_file_context"],
            elapsed_ms=0,
        )
    )
    trace.append(
        Step(
            step_number=1,
            purpose="Find the retry policy declaration.",
            elapsed_ms=5,
        )
    )
    trace.append(
        ToolCall(
            call_id="call-001",
            tool_name="search_code",
            arguments={"query": "retry policy"},
            elapsed_ms=10,
        )
    )
    trace.append(
        ToolResult(
            call_id="call-001",
            status="success",
            result={"matches": [{"path": "src/retry.py", "line": 12}]},
            elapsed_ms=15,
        )
    )
    trace.finalize(
        FinalAnswer(
            answer="The retry policy is declared in src/retry.py.",
            evidence=["src/retry.py:12"],
            uncertainties=[],
            next_queries=[],
            termination_reason="answer_complete",
            elapsed_ms=20,
        )
    )
    return trace


def test_normal_task_records_expected_event_flow() -> None:
    trace = build_completed_trace()

    assert [type(event) for event in trace.events] == [
        Session,
        Step,
        ToolCall,
        ToolResult,
        FinalAnswer,
    ]
    assert [event.event_type for event in trace.events] == [
        "session",
        "step",
        "tool_call",
        "tool_result",
        "final_answer",
    ]


def test_recorder_assigns_one_task_id_and_continuous_sequences() -> None:
    trace = build_completed_trace()

    assert {event.task_id for event in trace.events} == {"demo-task-001"}
    assert [event.sequence for event in trace.events] == [1, 2, 3, 4, 5]


def test_cannot_append_after_final_answer() -> None:
    trace = build_completed_trace()

    with pytest.raises(RuntimeError, match="already been finalized"):
        trace.append(Step(step_number=2, purpose="This must not be recorded."))


def test_tool_result_correlates_with_its_tool_call() -> None:
    trace = build_completed_trace()
    tool_call = next(event for event in trace.events if isinstance(event, ToolCall))
    tool_result = next(event for event in trace.events if isinstance(event, ToolResult))

    assert tool_result.call_id == tool_call.call_id == "call-001"


def test_json_round_trip_preserves_event_types_order_and_content() -> None:
    trace = build_completed_trace()

    restored = TraceRecorder.from_json(trace.to_json())

    assert restored.to_dict() == trace.to_dict()
    assert [type(event) for event in restored.events] == [type(event) for event in trace.events]
    assert [event.sequence for event in restored.events] == [1, 2, 3, 4, 5]
    assert restored.to_json() == trace.to_json()
