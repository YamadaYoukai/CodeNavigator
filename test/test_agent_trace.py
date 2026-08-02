import pytest

from src.examples.code_understanding_agent import (
    FinalAnswer,
    ModelRequest,
    ModelResult,
    Session,
    Step,
    ToolCall,
    ToolResult,
    TraceRecorder,
)


def test_model_result_enforces_success_and_error_payloads() -> None:
    success = ModelResult(
        request_id="model-request-001",
        status="success",
        elapsed_ms=125,
        decision={
            "decision_type": "final_answer",
            "answer": "The answer is grounded in the supplied evidence.",
        },
    )
    failure = ModelResult(
        request_id="model-request-002",
        status="error",
        elapsed_ms=250,
        error_type="invalid_model_output",
    )

    assert success.error_type is None
    assert failure.decision is None

    with pytest.raises(ValueError, match="elapsed_ms"):
        ModelResult(
            request_id="model-request-without-duration",
            status="error",
            error_type="model_execution_error",
        )

    with pytest.raises(ValueError, match="successful model result must include decision"):
        ModelResult(
            request_id="model-request-003",
            status="success",
            elapsed_ms=1,
        )

    with pytest.raises(ValueError, match="cannot include error_type"):
        ModelResult(
            request_id="model-request-004",
            status="success",
            elapsed_ms=2,
            decision={"decision_type": "final_answer", "answer": "Complete."},
            error_type="invalid_model_output",
        )

    with pytest.raises(ValueError, match="error model result must include error_type"):
        ModelResult(
            request_id="model-request-005",
            status="error",
            elapsed_ms=3,
        )

    with pytest.raises(ValueError, match="cannot include decision"):
        ModelResult(
            request_id="model-request-006",
            status="error",
            elapsed_ms=4,
            decision={"raw_response": "must not be retained"},
            error_type="model_execution_error",
        )


def test_model_request_and_result_round_trip_with_one_request_id() -> None:
    trace = TraceRecorder(task_id="model-task-001")
    trace.append(Session(user_task="Locate make_context.", available_tools=[]))
    trace.append(Step(step_number=1, purpose="Ask the model for one decision."))
    request = trace.append(
        ModelRequest(
            request_id="model-request-001",
            model="test-model",
            model_input={"current_task": "Locate make_context."},
        )
    )
    result = trace.append(
        ModelResult(
            request_id="model-request-001",
            status="success",
            elapsed_ms=321,
            decision={
                "decision_type": "tool_call",
                "call_id": "tool-call-001",
                "tool_name": "search_code",
                "arguments": {"query": "make_context"},
            },
        )
    )

    restored = TraceRecorder.from_json(trace.to_json())

    assert request.request_id == result.request_id == "model-request-001"
    assert result.elapsed_ms == 321
    assert result.decision["call_id"] != request.request_id
    assert {event.task_id for event in restored.events} == {"model-task-001"}
    assert [event.sequence for event in restored.events] == [1, 2, 3, 4]
    assert restored.to_json() == trace.to_json()
    assert restored.is_finalized is False


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
