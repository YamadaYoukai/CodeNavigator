import pytest
from pydantic import TypeAdapter, ValidationError

from src.examples.code_understanding_agent import (
    ContextState,
    FakeModel,
    FinalAnswerCitation,
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
            "evidence": [
                {
                    "repo": "example/repository",
                    "path": "src/retry.py",
                    "line": 12,
                }
            ],
            "uncertainties": [],
            "next_queries": ["RetryPolicy"],
        }
    )

    assert isinstance(decision, FinalAnswerDecision)
    assert decision.evidence == (
        FinalAnswerCitation(
            repo="example/repository",
            path="src/retry.py",
            line=12,
        ),
    )
    assert decision.evidence[0].to_canonical() == (
        "example/repository/src/retry.py:12"
    )
    assert decision.next_queries == ("RetryPolicy",)


@pytest.mark.parametrize(
    "evidence",
    [
        pytest.param(["example/repository/src/retry.py:12"], id="legacy-string"),
        pytest.param(
            ["example/repository/src/retry.py:12-14"],
            id="legacy-range-string",
        ),
        pytest.param(
            [{"repo": "click", "path": "src/retry.py", "start": 12, "end": 14}],
            id="range-fields",
        ),
        pytest.param(
            [{"repo": "click", "path": "src/retry.py", "line": 12, "quote": "x"}],
            id="extra-field",
        ),
        pytest.param([{"path": "src/retry.py", "line": 12}], id="missing-repo"),
        pytest.param([{"repo": "click", "line": 12}], id="missing-path"),
        pytest.param(
            [{"repo": "click", "path": "src/retry.py"}],
            id="missing-line",
        ),
        pytest.param(
            [{"repo": "", "path": "src/retry.py", "line": 12}],
            id="empty-repo",
        ),
        pytest.param(
            [{"repo": "click", "path": "", "line": 12}],
            id="empty-path",
        ),
        pytest.param(
            [{"repo": " click", "path": "src/retry.py", "line": 12}],
            id="surrounding-whitespace",
        ),
        pytest.param(
            [{"repo": "click", "path": "/src/retry.py", "line": 12}],
            id="absolute-path",
        ),
        pytest.param(
            [{"repo": "click", "path": r"src\retry.py", "line": 12}],
            id="backslash",
        ),
        pytest.param(
            [{"repo": "click", "path": "src\nretry.py", "line": 12}],
            id="newline",
        ),
        pytest.param(
            [{"repo": "click", "path": "src/../retry.py", "line": 12}],
            id="parent-segment",
        ),
        pytest.param(
            [{"repo": "click", "path": "src/./retry.py", "line": 12}],
            id="current-segment",
        ),
        pytest.param(
            [{"repo": "click", "path": "src//retry.py", "line": 12}],
            id="empty-segment",
        ),
        pytest.param(
            [{"repo": "click", "path": "src/retry.py:12", "line": 12}],
            id="colon",
        ),
        pytest.param(
            [{"repo": "click", "path": "src/retry.py", "line": 0}],
            id="zero-line",
        ),
        pytest.param(
            [{"repo": "click", "path": "src/retry.py", "line": -1}],
            id="negative-line",
        ),
        pytest.param(
            [{"repo": "click", "path": "src/retry.py", "line": True}],
            id="boolean-line",
        ),
        pytest.param(
            [{"repo": "click", "path": "src/retry.py", "line": "12"}],
            id="string-line",
        ),
    ],
)
def test_should_reject_non_exact_final_answer_citations(
    evidence: list[object],
) -> None:
    adapter = TypeAdapter(ModelDecision)

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "decision_type": "final_answer",
                "answer": "Unsupported citation shape.",
                "evidence": evidence,
                "uncertainties": [],
                "next_queries": [],
            }
        )


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
        evidence=(
            FinalAnswerCitation(
                repo="example/repository",
                path="src/retry.py",
                line=12,
            ),
        ),
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
