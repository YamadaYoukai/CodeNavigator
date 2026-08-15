import asyncio
from collections import Counter
from collections.abc import Callable
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
    FinalAnswerDecision,
    GET_FILE_CONTEXT,
    GetFileContextArguments,
    MAX_TOOL_CALLS_PER_TASK,
    ModelInput,
    ModelRequest,
    ModelResult,
    PydanticToolAdapter,
    SEARCH_CODE,
    SearchCodeArguments,
    Step,
    ToolCall,
    ToolCallDecision,
    ToolErrorCode,
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
                    "repo": "example/repository",
                    "path": "src/retry.py",
                    "line": 12,
                    "snippet": "class RetryPolicy:",
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


class ScriptedModelBoundary:
    """Small script that can return invalid objects or raise raw failures."""

    def __init__(self, outcomes: tuple[object, ...]) -> None:
        self._outcomes = outcomes
        self._next_outcome = 0
        self.calls = 0
        self.remaining_budgets: list[int] = []

    @property
    def remaining_outcomes(self) -> int:
        return len(self._outcomes) - self._next_outcome

    def decide(self, model_input: ModelInput) -> object:
        self.calls += 1
        self.remaining_budgets.append(model_input.remaining_tool_calls)
        outcome = self._outcomes[self._next_outcome]
        self._next_outcome += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class MissingToolResultRouter:
    """Violate the Tool executor post-condition without fabricating events."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, _: ToolCall) -> object:
        self.calls += 1
        return None


def build_loop_state(*, remaining_tool_calls: int) -> ContextState:
    return ContextState(
        system_instruction="Answer only from supplied code evidence.",
        current_task="Locate the retry policy.",
        evidence_item_budget=3,
        remaining_tool_calls=remaining_tool_calls,
    )


def build_loop_for_script(
    *,
    trace: TraceRecorder,
    model: ScriptedModelBoundary,
    search_handler: Callable[..., object],
    tool_step_executor: ToolStepExecutor | None = None,
) -> AgentLoop:
    context_builder = ContextBuilder()
    request_numbers = count(1)
    traced_model = TracedModelClient(
        client=model,  # type: ignore[arg-type]
        model="scripted-agent-model",
        trace=trace,
        request_id_factory=lambda: f"request-{next(request_numbers)}",
        clock=lambda: 0.0,
    )

    if tool_step_executor is None:
        def get_file_context(**_: object) -> dict[str, object]:
            return {"content": "unused"}

        router = ToolRouter(
            trace=trace,
            tools={
                SEARCH_CODE: PydanticToolAdapter(
                    SearchCodeArguments,
                    search_handler,
                ),
                GET_FILE_CONTEXT: PydanticToolAdapter(
                    GetFileContextArguments,
                    get_file_context,
                ),
            },
        )
        tool_step_executor = ToolStepExecutor(
            router=router,
            context_builder=context_builder,
        )

    return AgentLoop(
        context_builder=context_builder,
        model=traced_model,
        tool_step_executor=tool_step_executor,
        trace=trace,
    )


def assert_failure_terminal(
    *,
    trace: TraceRecorder,
    outcome: object,
    reason: str,
    private_marker: str | None = None,
) -> None:
    assert hasattr(outcome, "final_answer")
    final_answer = outcome.final_answer
    events = trace.events
    final_events = [event for event in events if isinstance(event, FinalAnswer)]

    assert len(final_events) == 1
    assert final_events[0] == final_answer == events[-1]
    assert final_answer.termination_reason == reason
    assert final_answer.evidence == []
    assert final_answer.uncertainties == []
    assert final_answer.next_queries == []
    assert trace.is_finalized is True
    assert {event.task_id for event in events} == {trace.task_id}
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))

    serialized_outputs = (
        trace.to_json()
        + outcome.final_state.model_dump_json()
        + final_answer.model_dump_json()
    )
    if private_marker is not None:
        assert private_marker not in serialized_outputs

    event_count = len(events)
    with pytest.raises(RuntimeError, match="already been finalized"):
        trace.append(Step(step_number=99, purpose="must not be appended"))
    assert len(trace.events) == event_count


def test_should_terminate_without_tool_events_when_initial_budget_is_zero() -> None:
    trace = TraceRecorder(task_id="agent-loop-budget-zero")
    model = ScriptedModelBoundary(
        (
            ToolCallDecision(
                call_id="call-disallowed",
                tool_name=SEARCH_CODE,
                arguments={"query": "retry policy"},
            ),
            FinalAnswerDecision(answer="must not be requested"),
        )
    )
    tool_invocations = 0

    def search_code(**_: object) -> dict[str, object]:
        nonlocal tool_invocations
        tool_invocations += 1
        return {"matches": []}

    loop = build_loop_for_script(
        trace=trace,
        model=model,
        search_handler=search_code,
    )

    outcome = asyncio.run(loop.run(build_loop_state(remaining_tool_calls=0)))

    assert_failure_terminal(
        trace=trace,
        outcome=outcome,
        reason="tool_budget_exhausted",
    )
    assert [event.event_type for event in trace.events] == [
        "session",
        "step",
        "model_request",
        "model_result",
        "final_answer",
    ]
    assert not any(isinstance(event, (ToolCall, ToolResult)) for event in trace.events)
    assert tool_invocations == 0
    assert outcome.tool_calls_used == 0
    assert outcome.final_state.remaining_tool_calls == 0
    assert model.calls == 1
    assert model.remaining_budgets == [0]
    assert model.remaining_outcomes == 1


def test_should_stop_after_six_attempts_when_model_requests_a_seventh_tool() -> None:
    trace = TraceRecorder(task_id="agent-loop-budget-six")
    tool_decisions = tuple(
        ToolCallDecision(
            call_id=f"call-{index}",
            tool_name=SEARCH_CODE,
            arguments={"query": f"retry policy {index}"},
        )
        for index in range(1, MAX_TOOL_CALLS_PER_TASK + 2)
    )
    model = ScriptedModelBoundary(
        (*tool_decisions, FinalAnswerDecision(answer="must not be requested"))
    )
    tool_invocations = 0

    def search_code(**_: object) -> dict[str, object]:
        nonlocal tool_invocations
        tool_invocations += 1
        return {"matches": []}

    loop = build_loop_for_script(
        trace=trace,
        model=model,
        search_handler=search_code,
    )

    outcome = asyncio.run(loop.run(build_loop_state(remaining_tool_calls=99)))

    assert_failure_terminal(
        trace=trace,
        outcome=outcome,
        reason="tool_budget_exhausted",
    )
    assert tool_invocations == MAX_TOOL_CALLS_PER_TASK
    assert outcome.tool_calls_used == MAX_TOOL_CALLS_PER_TASK
    assert outcome.final_state.remaining_tool_calls == 0
    assert model.calls == MAX_TOOL_CALLS_PER_TASK + 1
    assert model.remaining_budgets == [6, 5, 4, 3, 2, 1, 0]
    assert model.remaining_outcomes == 1
    assert len([event for event in trace.events if isinstance(event, ToolCall)]) == 6
    assert len([event for event in trace.events if isinstance(event, ToolResult)]) == 6
    assert len([event for event in trace.events if isinstance(event, ModelResult)]) == 7
    assert isinstance(trace.events[-2], ModelResult)
    assert trace.events[-2].decision["call_id"] == "call-7"


@pytest.mark.parametrize(
    ("trigger", "reason", "error_type", "private_marker"),
    [
        pytest.param(
            RuntimeError("private-model-execution-marker"),
            "model_execution_error",
            "model_execution_error",
            "private-model-execution-marker",
            id="model-execution-error",
        ),
        pytest.param(
            {"raw_provider_response": "private-invalid-output-marker"},
            "invalid_model_output",
            "invalid_model_output",
            "private-invalid-output-marker",
            id="invalid-model-output",
        ),
        pytest.param(
            TimeoutError("private-model-timeout-marker"),
            "model_timeout",
            "model_timeout",
            "private-model-timeout-marker",
            id="model-timeout",
        ),
    ],
)
def test_should_terminate_model_failures_without_retry_or_private_data(
    trigger: object,
    reason: str,
    error_type: str,
    private_marker: str,
) -> None:
    trace = TraceRecorder(task_id=f"agent-loop-{reason}")
    model = ScriptedModelBoundary(
        (trigger, FinalAnswerDecision(answer="must not be requested"))
    )

    def search_code(**_: object) -> object:
        pytest.fail("a model failure must not invoke a Tool")

    loop = build_loop_for_script(
        trace=trace,
        model=model,
        search_handler=search_code,
    )

    outcome = asyncio.run(loop.run(build_loop_state(remaining_tool_calls=2)))

    assert_failure_terminal(
        trace=trace,
        outcome=outcome,
        reason=reason,
        private_marker=private_marker,
    )
    assert [event.event_type for event in trace.events] == [
        "session",
        "step",
        "model_request",
        "model_result",
        "final_answer",
    ]
    request, result = trace.events[-3:-1]
    assert isinstance(request, ModelRequest)
    assert isinstance(result, ModelResult)
    assert request.request_id == result.request_id
    assert result.status == "error"
    assert result.error_type == error_type
    assert result.elapsed_ms >= 0
    assert not any(isinstance(event, (ToolCall, ToolResult)) for event in trace.events)
    assert outcome.tool_calls_used == 0
    assert model.calls == 1
    assert model.remaining_outcomes == 1


@pytest.mark.parametrize(
    ("exception_type", "reason", "error_type", "private_marker"),
    [
        pytest.param(
            RuntimeError,
            "tool_error",
            ToolErrorCode.TOOL_EXECUTION_ERROR.value,
            "private-tool-error-marker",
            id="tool-error",
        ),
        pytest.param(
            TimeoutError,
            "tool_timeout",
            ToolErrorCode.TOOL_TIMEOUT.value,
            "private-tool-timeout-marker",
            id="tool-timeout",
        ),
    ],
)
def test_should_terminate_tool_failures_after_one_consumed_attempt(
    exception_type: type[Exception],
    reason: str,
    error_type: str,
    private_marker: str,
) -> None:
    trace = TraceRecorder(task_id=f"agent-loop-{reason}")
    model = ScriptedModelBoundary(
        (
            ToolCallDecision(
                call_id=f"call-{reason}",
                tool_name=SEARCH_CODE,
                arguments={"query": "retry policy"},
            ),
            FinalAnswerDecision(answer="must not be requested"),
        )
    )
    tool_invocations = 0

    def search_code(**_: object) -> object:
        nonlocal tool_invocations
        tool_invocations += 1
        raise exception_type(private_marker)

    loop = build_loop_for_script(
        trace=trace,
        model=model,
        search_handler=search_code,
    )

    outcome = asyncio.run(loop.run(build_loop_state(remaining_tool_calls=2)))

    assert_failure_terminal(
        trace=trace,
        outcome=outcome,
        reason=reason,
        private_marker=private_marker,
    )
    assert [event.event_type for event in trace.events] == [
        "session",
        "step",
        "model_request",
        "model_result",
        "tool_call",
        "tool_result",
        "final_answer",
    ]
    call, result = trace.events[-3:-1]
    assert isinstance(call, ToolCall)
    assert isinstance(result, ToolResult)
    assert call.call_id == result.call_id == f"call-{reason}"
    assert result.status == "error"
    assert result.error_type == error_type
    assert outcome.tool_calls_used == 1
    assert outcome.final_state.remaining_tool_calls == 1
    assert error_type in outcome.final_state.evidence[0].content
    assert tool_invocations == 1
    assert model.calls == 1
    assert model.remaining_outcomes == 1


def test_should_finalize_harness_invariant_without_fabricating_tool_result() -> None:
    trace = TraceRecorder(task_id="agent-loop-harness-invariant")
    model = ScriptedModelBoundary(
        (
            ToolCallDecision(
                call_id="call-missing-result",
                tool_name=SEARCH_CODE,
                arguments={"query": "retry policy"},
            ),
            FinalAnswerDecision(answer="must not be requested"),
        )
    )
    missing_router = MissingToolResultRouter()
    context_builder = ContextBuilder()
    tool_step_executor = ToolStepExecutor(
        router=missing_router,  # type: ignore[arg-type]
        context_builder=context_builder,
    )
    loop = build_loop_for_script(
        trace=trace,
        model=model,
        search_handler=lambda **_: {"matches": []},
        tool_step_executor=tool_step_executor,
    )

    outcome = asyncio.run(loop.run(build_loop_state(remaining_tool_calls=1)))

    assert_failure_terminal(
        trace=trace,
        outcome=outcome,
        reason="harness_invariant_error",
    )
    assert [event.event_type for event in trace.events] == [
        "session",
        "step",
        "model_request",
        "model_result",
        "final_answer",
    ]
    assert not any(isinstance(event, (ToolCall, ToolResult)) for event in trace.events)
    assert missing_router.calls == 1
    assert outcome.tool_calls_used == 1
    assert outcome.final_state.remaining_tool_calls == 1
    assert model.calls == 1
    assert model.remaining_outcomes == 1
