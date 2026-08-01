import pytest
from pydantic import TypeAdapter, ValidationError

from src.examples.code_understanding_agent import (
    ContextState,
    FakeModel,
    FinalAnswerDecision,
    GET_FILE_CONTEXT,
    ModelDecision,
    ModelInput,
    SEARCH_CODE,
    ToolCallDecision,
    build_context,
)


def build_model_input(task: str = "Locate the retry policy.") -> ModelInput:
    return build_context(
        ContextState(
            system_instruction="Answer only from supplied code evidence.",
            current_task=task,
            evidence_item_budget=0,
            remaining_tool_calls=1,
        )
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        pytest.param(
            SEARCH_CODE,
            {"query": "retry policy", "limit": 3, "literal": True},
            id="search-code",
        ),
        pytest.param(
            GET_FILE_CONTEXT,
            {
                "repository": "example/repository",
                "file_path": "src/retry.py",
                "line_number": 12,
                "lines_before": 0,
                "lines_after": 100,
            },
            id="get-file-context",
        ),
    ],
)
def test_should_accept_valid_arguments_for_each_tool(
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    decision = ToolCallDecision(
        call_id="call-001",
        tool_name=tool_name,
        arguments=arguments,
    )

    assert decision.tool_name == tool_name
    assert decision.arguments == arguments


def test_should_reject_tool_names_outside_the_allowlist() -> None:
    with pytest.raises(ValidationError):
        ToolCallDecision(
            call_id="call-unknown",
            tool_name="write_file",
            arguments={},
        )


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        pytest.param(SEARCH_CODE, {"query": ""}, id="empty-search-query"),
        pytest.param(SEARCH_CODE, {}, id="missing-search-query"),
        pytest.param(
            SEARCH_CODE,
            {"query": "retry policy", "unknown": True},
            id="unknown-search-field",
        ),
        pytest.param(
            GET_FILE_CONTEXT,
            {
                "repository": "",
                "file_path": "src/retry.py",
                "line_number": 12,
            },
            id="empty-repository",
        ),
        pytest.param(
            GET_FILE_CONTEXT,
            {
                "repository": "example/repository",
                "file_path": "",
                "line_number": 12,
            },
            id="empty-file-path",
        ),
        pytest.param(
            GET_FILE_CONTEXT,
            {
                "repository": "example/repository",
                "file_path": "src/retry.py",
                "line_number": 0,
            },
            id="non-positive-line-number",
        ),
        pytest.param(
            GET_FILE_CONTEXT,
            {
                "repository": "example/repository",
                "file_path": "src/retry.py",
                "line_number": 12,
                "unknown": True,
            },
            id="unknown-file-context-field",
        ),
    ],
)
def test_should_reject_arguments_that_violate_the_selected_tool_contract(
    tool_name: str,
    arguments: dict[str, object],
) -> None:

    with pytest.raises(ValidationError):
        ToolCallDecision(
            call_id="call-invalid",
            tool_name=tool_name,
            arguments=arguments,
        )


def test_should_discriminate_and_validate_a_structured_final_answer() -> None:
    adapter = TypeAdapter(ModelDecision)

    decision = adapter.validate_python(
        {
            "decision_type": "final_answer",
            "answer": "The retry policy is in src/retry.py.",
            "evidence": ["src/retry.py:12"],
            "uncertainties": [],
            "next_queries": ["RetryPolicy"],
        }
    )

    assert isinstance(decision, FinalAnswerDecision)
    assert decision.evidence == ("src/retry.py:12",)
    assert decision.next_queries == ("RetryPolicy",)


def test_should_replay_scripted_decisions_and_record_detached_input_snapshots() -> None:
    first_input = build_model_input()
    second_input = build_model_input("Explain how RetryPolicy is used.")
    scripted_tool_call = ToolCallDecision(
        call_id="call-001",
        tool_name=SEARCH_CODE,
        arguments={"query": "RetryPolicy"},
    )
    scripted_final_answer = FinalAnswerDecision(
        answer="RetryPolicy controls retry attempts.",
        evidence=("src/retry.py:12",),
    )
    fake = FakeModel([scripted_tool_call, scripted_final_answer])

    first_decision = fake.decide(first_input)
    second_decision = fake.decide(second_input)
    first_input.tool_schemas[0].input_schema["tampered_after_call"] = True

    assert first_decision == scripted_tool_call
    assert second_decision == scripted_final_answer
    assert [item.current_task for item in fake.model_inputs] == [
        "Locate the retry policy.",
        "Explain how RetryPolicy is used.",
    ]
    assert "tampered_after_call" not in fake.model_inputs[0].tool_schemas[0].input_schema
    assert fake.model_inputs[0] is not first_input
    assert fake.remaining_decisions == 0


def test_should_fail_deterministically_when_fake_model_script_is_exhausted() -> None:
    fake = FakeModel(
        [FinalAnswerDecision(answer="No additional code lookup is required.")]
    )
    model_input = build_model_input()

    fake.decide(model_input)

    with pytest.raises(
        RuntimeError,
        match="fake model has no scripted decisions remaining",
    ):
        fake.decide(model_input)

    assert fake.model_inputs == (model_input,)
