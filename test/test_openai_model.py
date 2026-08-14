import json
from types import SimpleNamespace

import pytest

from src.examples.code_understanding_agent import (
    ContextState,
    FakeModel,
    FinalAnswerDecision,
    InvalidModelOutputError,
    ModelExecutionError,
    ModelRequest,
    ModelResult,
    ModelTimeoutError,
    OpenAIModel,
    RepositoryHint,
    SearchCodeArguments,
    ToolCallDecision,
    TraceRecorder,
    TracedModelClient,
    build_context,
)
from src.examples.code_understanding_agent.openai_model import build_messages, build_tools


class FakeCompletions:
    def __init__(self, *, response: object = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)
        self.api_key = "must-not-appear-in-trace"
        self.base_url = "https://must-not-appear-in-trace.invalid"
        self.default_headers = {"Authorization": "must-not-appear-in-trace"}


def build_model_input():
    return build_context(
        ContextState(
            system_instruction="Answer only from supplied code evidence.",
            current_task="Where is make_context implemented?",
            repository_hints=(
                RepositoryHint(
                    canonical_name="click",
                    aliases=("Click", "pallets/click"),
                ),
            ),
            evidence=(),
            evidence_item_budget=4,
            remaining_tool_calls=1,
        )
    )


def make_response(
    *,
    content: str | None = None,
    tool_calls: list[object] | None = None,
    refusal: str | None = None,
) -> object:
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        refusal=refusal,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_tool_call(call_id: str, name: str, arguments: str) -> object:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def build_adapter(response: object) -> tuple[OpenAIModel, FakeCompletions]:
    completions = FakeCompletions(response=response)
    return OpenAIModel(client=FakeClient(completions), model="test-model"), completions


def test_build_tools_converts_defaulted_pydantic_schemas_to_strict_tools() -> None:
    tools = build_tools(build_model_input().tool_schemas)

    assert [tool["function"]["name"] for tool in tools] == [
        "search_code",
        "get_file_context",
    ]
    for tool in tools:
        function = tool["function"]
        parameters = function["parameters"]
        assert function["strict"] is True
        assert parameters["additionalProperties"] is False
        assert parameters["required"] == list(parameters["properties"])
        assert "default" not in json.dumps(parameters)


def test_build_messages_supplies_closed_world_repository_names() -> None:
    system_message, user_message = build_messages(build_model_input())
    user_payload = json.loads(user_message["content"])

    assert "Never invent" in system_message["content"]
    assert user_payload["repository_hints"] == [
        {
            "canonical_name": "click",
            "aliases": ["Click", "pallets/click"],
        }
    ]


def test_valid_search_code_response_becomes_tool_call_decision() -> None:
    adapter, completions = build_adapter(
        make_response(
            tool_calls=[
                make_tool_call(
                    "tool-call-search",
                    "search_code",
                    json.dumps(
                        {
                            "query": "def make_context",
                            "repo": None,
                            "lang": "python",
                            "path": None,
                            "limit": 20,
                            "literal": True,
                        }
                    ),
                )
            ]
        )
    )

    decision = adapter.decide(build_model_input())

    assert isinstance(decision, ToolCallDecision)
    assert decision.call_id == "tool-call-search"
    assert decision.tool_name == "search_code"
    SearchCodeArguments.model_validate(decision.arguments)
    assert completions.calls[0]["model"] == "test-model"
    assert completions.calls[0]["tool_choice"] == "auto"
    assert completions.calls[0]["parallel_tool_calls"] is False


def test_valid_get_file_context_response_becomes_tool_call_decision() -> None:
    adapter, _ = build_adapter(
        make_response(
            tool_calls=[
                make_tool_call(
                    "tool-call-context",
                    "get_file_context",
                    json.dumps(
                        {
                            "repository": "pallets/click",
                            "file_path": "src/click/core.py",
                            "line_number": 1169,
                            "lines_before": 20,
                            "lines_after": 20,
                        }
                    ),
                )
            ]
        )
    )

    decision = adapter.decide(build_model_input())

    assert isinstance(decision, ToolCallDecision)
    assert decision.call_id == "tool-call-context"
    assert decision.tool_name == "get_file_context"


def test_valid_json_final_answer_becomes_final_answer_decision() -> None:
    adapter, _ = build_adapter(
        make_response(
            content=json.dumps(
                {
                    "decision_type": "final_answer",
                    "answer": "make_context is implemented in src/click/core.py.",
                    "evidence": ["src/click/core.py:1169"],
                    "uncertainties": [],
                    "next_queries": [],
                }
            )
        )
    )

    decision = adapter.decide(build_model_input())

    assert isinstance(decision, FinalAnswerDecision)
    assert decision.evidence == ("src/click/core.py:1169",)


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            make_response(
                tool_calls=[
                    make_tool_call("one", "search_code", '{"query":"one"}'),
                    make_tool_call("two", "search_code", '{"query":"two"}'),
                ]
            ),
            id="multiple-tool-calls",
        ),
        pytest.param(
            make_response(
                tool_calls=[make_tool_call("unknown", "write_file", "{}")]
            ),
            id="unknown-tool",
        ),
        pytest.param(
            make_response(
                tool_calls=[make_tool_call("bad-json", "search_code", "{")]
            ),
            id="invalid-json",
        ),
        pytest.param(
            make_response(
                tool_calls=[
                    make_tool_call("empty-query", "search_code", '{"query":""}')
                ]
            ),
            id="empty-query",
        ),
        pytest.param(make_response(content=None), id="empty-content"),
        pytest.param(make_response(refusal="I cannot comply."), id="refusal"),
        pytest.param(SimpleNamespace(choices=[]), id="empty-choices"),
    ],
)
def test_invalid_outputs_are_rejected_without_retaining_raw_response(
    response: object,
) -> None:
    adapter, _ = build_adapter(response)

    with pytest.raises(InvalidModelOutputError, match="invalid model output") as caught:
        adapter.decide(build_model_input())

    assert caught.value.__cause__ is None
    assert not hasattr(caught.value, "response")


def test_sdk_exception_is_classified_as_model_execution_error() -> None:
    completions = FakeCompletions(error=RuntimeError("raw SDK failure"))
    adapter = OpenAIModel(client=FakeClient(completions), model="test-model")

    with pytest.raises(ModelExecutionError, match="model execution failed") as caught:
        adapter.decide(build_model_input())

    assert caught.value.__cause__ is None
    assert "raw SDK failure" not in str(caught.value)


def test_sdk_timeout_is_classified_without_retaining_transport_details() -> None:
    completions = FakeCompletions(error=TimeoutError("private transport timeout"))
    adapter = OpenAIModel(client=FakeClient(completions), model="test-model")

    with pytest.raises(ModelTimeoutError, match="model execution timed out") as caught:
        adapter.decide(build_model_input())

    assert caught.value.__cause__ is None
    assert "private transport timeout" not in str(caught.value)


def test_traced_client_correlates_request_and_success_without_secrets() -> None:
    adapter, _ = build_adapter(
        make_response(
            tool_calls=[
                make_tool_call(
                    "tool-call-search",
                    "search_code",
                    json.dumps(
                        {
                            "query": "make_context",
                            "repo": None,
                            "lang": None,
                            "path": None,
                            "limit": 20,
                            "literal": False,
                        }
                    ),
                )
            ]
        )
    )
    trace = TraceRecorder(task_id="model-trace-success")
    traced = TracedModelClient(
        client=adapter,
        model="test-model",
        trace=trace,
        request_id_factory=lambda: "model-request-local",
        clock=iter((10.0, 10.123)).__next__,
    )

    decision = traced.decide(build_model_input())

    request, result = trace.events
    assert isinstance(request, ModelRequest)
    assert isinstance(result, ModelResult)
    assert request.request_id == result.request_id == "model-request-local"
    assert decision.call_id == "tool-call-search"
    assert decision.call_id != request.request_id
    assert result.status == "success"
    assert result.elapsed_ms == 123
    assert result.decision == decision.model_dump(mode="json")
    assert result.error_type is None
    serialized = trace.to_json()
    assert "must-not-appear-in-trace" not in serialized
    assert "base_url" not in serialized
    assert "headers" not in serialized


def test_traced_client_records_only_stable_error_type_and_reraises_boundary_error() -> None:
    completions = FakeCompletions(error=RuntimeError("raw SDK response and headers"))
    adapter = OpenAIModel(client=FakeClient(completions), model="test-model")
    trace = TraceRecorder(task_id="model-trace-error")
    traced = TracedModelClient(
        client=adapter,
        model="test-model",
        trace=trace,
        request_id_factory=lambda: "model-request-error",
        clock=iter((20.0, 20.25)).__next__,
    )

    with pytest.raises(ModelExecutionError):
        traced.decide(build_model_input())

    request, result = trace.events
    assert request.request_id == result.request_id == "model-request-error"
    assert result.status == "error"
    assert result.elapsed_ms == 250
    assert result.error_type == "model_execution_error"
    assert result.decision is None
    assert "raw SDK response" not in trace.to_json()


def test_traced_client_records_invalid_output_duration_and_stable_error() -> None:
    adapter, _ = build_adapter(make_response(content=None))
    trace = TraceRecorder(task_id="model-trace-invalid-output")
    traced = TracedModelClient(
        client=adapter,
        model="test-model",
        trace=trace,
        request_id_factory=lambda: "model-request-invalid-output",
        clock=iter((25.0, 25.075)).__next__,
    )

    with pytest.raises(InvalidModelOutputError):
        traced.decide(build_model_input())

    request, result = trace.events
    assert request.request_id == result.request_id == "model-request-invalid-output"
    assert result.status == "error"
    assert result.elapsed_ms == 75
    assert result.error_type == "invalid_model_output"
    assert result.decision is None


def test_traced_client_records_correlated_model_timeout_without_retry() -> None:
    private_marker = "private traced timeout detail"
    completions = FakeCompletions(error=TimeoutError(private_marker))
    adapter = OpenAIModel(client=FakeClient(completions), model="test-model")
    trace = TraceRecorder(task_id="model-trace-timeout")
    traced = TracedModelClient(
        client=adapter,
        model="test-model",
        trace=trace,
        request_id_factory=lambda: "model-request-timeout",
        clock=iter((30.0, 29.0)).__next__,
    )

    with pytest.raises(ModelTimeoutError):
        traced.decide(build_model_input())

    request, result = trace.events
    assert request.request_id == result.request_id == "model-request-timeout"
    assert result.status == "error"
    assert result.elapsed_ms == 0
    assert result.error_type == "model_timeout"
    assert result.decision is None
    assert len(completions.calls) == 1
    assert private_marker not in trace.to_json()


def test_traced_client_can_wrap_fake_model_without_provider_dependencies() -> None:
    scripted = FinalAnswerDecision(
        answer="The supplied evidence is sufficient.",
        evidence=("src/click/core.py:1169",),
    )
    trace = TraceRecorder(task_id="traced-fake-model")
    traced = TracedModelClient(
        client=FakeModel([scripted]),
        model="fake-model",
        trace=trace,
        request_id_factory=lambda: "fake-model-request",
        clock=iter((30.0, 30.001)).__next__,
    )

    decision = traced.decide(build_model_input())

    assert decision == scripted
    assert [event.event_type for event in trace.events] == [
        "model_request",
        "model_result",
    ]
    assert trace.events[0].request_id == trace.events[1].request_id
    assert trace.events[1].elapsed_ms == 1
