import pytest
from pydantic import ValidationError

from src.examples.code_understanding_agent import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    ContextBuilder,
    ContextState,
    Evidence,
    EvidenceKind,
    RepositoryHint,
    build_context,
)


def build_state(**overrides: object) -> ContextState:
    payload: dict[str, object] = {
        "system_instruction": "Answer only from the supplied code evidence.",
        "current_task": "Locate the retry policy.",
        "repository_hints": (
            RepositoryHint(
                canonical_name="retry-service",
                aliases=("example/retry-service",),
            ),
        ),
        "evidence": (
            Evidence(
                kind=EvidenceKind.FACT,
                source="search_code:src/retry.py:12",
                content="RETRY_LIMIT = 3",
            ),
        ),
        "evidence_item_budget": 4,
        "remaining_tool_calls": 1,
    }
    payload.update(overrides)
    return ContextState.model_validate(payload)


def test_should_include_every_required_contract_field_when_building_full_context() -> None:
    context = build_context(build_state())
    payload = context.to_payload()

    assert list(payload) == [
        "system_instruction",
        "current_task",
        "repository_hints",
        "tool_schemas",
        "evidence",
        "remaining_tool_calls",
    ]
    assert payload["system_instruction"] == "Answer only from the supplied code evidence."
    assert payload["current_task"] == "Locate the retry policy."
    assert payload["repository_hints"] == [
        {
            "canonical_name": "retry-service",
            "aliases": ["example/retry-service"],
        }
    ]
    assert [schema["name"] for schema in payload["tool_schemas"]] == [
        SEARCH_CODE,
        GET_FILE_CONTEXT,
    ]
    assert all("properties" in schema["input_schema"] for schema in payload["tool_schemas"])
    assert payload["evidence"] == [
        {
            "kind": "fact",
            "source": "search_code:src/retry.py:12",
            "content": "RETRY_LIMIT = 3",
        }
    ]
    assert payload["remaining_tool_calls"] == 1


def test_should_keep_facts_and_drop_low_value_history_when_evidence_budget_is_small() -> None:
    state = build_state(
        evidence=(
            Evidence(
                kind=EvidenceKind.HISTORY,
                source="step:1",
                content="First I considered a broad query.",
            ),
            Evidence(
                kind=EvidenceKind.FACT,
                source="search_code:src/retry.py:12",
                content="RETRY_LIMIT = 3",
            ),
            Evidence(
                kind=EvidenceKind.HISTORY,
                source="step:2",
                content="Then I planned another search.",
            ),
            Evidence(
                kind=EvidenceKind.FACT,
                source="get_file_context:src/retry.py:12",
                content="RetryPolicy uses RETRY_LIMIT.",
            ),
        ),
        evidence_item_budget=2,
        remaining_tool_calls=0,
    )

    context = ContextBuilder().build(state)

    assert [(item.kind, item.source) for item in context.evidence] == [
        (EvidenceKind.FACT, "search_code:src/retry.py:12"),
        (EvidenceKind.FACT, "get_file_context:src/retry.py:12"),
    ]
    assert context.remaining_tool_calls == 0


def test_should_keep_output_order_and_input_state_stable_for_identical_state() -> None:
    state = build_state(
        evidence=(
            Evidence(
                kind=EvidenceKind.HISTORY,
                source="step:1",
                content="Investigate retry behavior.",
            ),
            Evidence(
                kind=EvidenceKind.FACT,
                source="search_code:src/retry.py:12",
                content="RETRY_LIMIT = 3",
            ),
            Evidence(
                kind=EvidenceKind.FACT,
                source="get_file_context:src/retry.py:12",
                content="RetryPolicy uses RETRY_LIMIT.",
            ),
        ),
        evidence_item_budget=3,
    )
    original_state = state.model_dump(mode="json")
    builder = ContextBuilder()

    first = builder.build(state)
    second = builder.build(state)

    assert first.to_json() == second.to_json()
    assert [schema.name for schema in first.tool_schemas] == [
        SEARCH_CODE,
        GET_FILE_CONTEXT,
    ]
    assert first.repository_hints == state.repository_hints
    assert first.repository_hints[0] is not state.repository_hints[0]
    assert [item.kind for item in first.evidence] == [
        EvidenceKind.FACT,
        EvidenceKind.FACT,
        EvidenceKind.HISTORY,
    ]
    assert state.model_dump(mode="json") == original_state


def test_should_reject_duplicate_canonical_repository_names() -> None:
    with pytest.raises(ValidationError, match="unique canonical names"):
        build_state(
            repository_hints=(
                RepositoryHint(canonical_name="click"),
                RepositoryHint(canonical_name="click", aliases=("pallets/click",)),
            )
        )


@pytest.mark.parametrize(
    "aliases",
    [("",), (" pallets/click",), ("pallets/click", "pallets/click")],
)
def test_should_reject_empty_or_duplicate_aliases(aliases: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="repository aliases"):
        RepositoryHint(canonical_name="click", aliases=aliases)


@pytest.mark.parametrize("canonical_name", ["", " ", " click", "click "])
def test_should_reject_invalid_canonical_repository_name(
    canonical_name: str,
) -> None:
    with pytest.raises(ValidationError):
        RepositoryHint(canonical_name=canonical_name)
