from src.examples.code_understanding_agent import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    ContextBuilder,
    ContextState,
    Evidence,
    EvidenceKind,
    build_context,
)


def build_state(**overrides: object) -> ContextState:
    payload: dict[str, object] = {
        "system_instruction": "Answer only from the supplied code evidence.",
        "current_task": "Locate the retry policy.",
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
        "tool_schemas",
        "evidence",
        "remaining_tool_calls",
    ]
    assert payload["system_instruction"] == "Answer only from the supplied code evidence."
    assert payload["current_task"] == "Locate the retry policy."
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
    assert [item.kind for item in first.evidence] == [
        EvidenceKind.FACT,
        EvidenceKind.FACT,
        EvidenceKind.HISTORY,
    ]
    assert state.model_dump(mode="json") == original_state
