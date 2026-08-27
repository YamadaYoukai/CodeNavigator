import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.examples.code_understanding_agent import (
    ContextBuilder,
    ContextState,
    EvidenceKind,
    FakeModel,
    IncidentContextValidationError,
    IncidentExtractionCandidate,
    IncidentExtractionResult,
    IncidentInput,
    IncidentRetrievalTask,
    RepositoryHint,
    SearchCodeArguments,
    SourcedIncidentValue,
    ToolRouter,
    map_incident_to_retrieval_context,
    validate_incident_extraction,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "evaluation"
    / "fixtures"
    / "incident-extraction-public-cases-2026-08-26.json"
)
SYSTEM_INSTRUCTION = "Use only the supplied incident sources and code evidence."


def load_public_case(case_id: str) -> dict[str, Any]:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return next(case for case in cases if case["case_id"] == case_id)


def validated_public_case(
    case_id: str,
) -> tuple[IncidentInput, IncidentExtractionResult]:
    case = load_public_case(case_id)
    incident_input = IncidentInput.model_validate_json(
        json.dumps(case["input"], ensure_ascii=False, separators=(",", ":")),
        strict=True,
    )
    candidate = IncidentExtractionCandidate.model_validate_json(
        json.dumps(case["candidate"], ensure_ascii=False, separators=(",", ":")),
        strict=True,
    )
    return incident_input, validate_incident_extraction(incident_input, candidate)


def map_public_case(
    case_id: str,
    *,
    repository_hints: list[RepositoryHint] | tuple[RepositoryHint, ...] = (),
    selected_repository: str | None = None,
    remaining_tool_calls: int = 1,
) -> tuple[tuple[IncidentRetrievalTask, ...], ContextState]:
    incident_input, result = validated_public_case(case_id)
    return map_incident_to_retrieval_context(
        incident_input,
        result,
        system_instruction=SYSTEM_INSTRUCTION,
        repository_hints=repository_hints,
        selected_repository=selected_repository,
        evidence_item_budget=len(incident_input.sources),
        remaining_tool_calls=remaining_tool_calls,
    )


def stable_json(value: object) -> str:
    def to_json_value(item: object) -> object:
        if hasattr(item, "model_dump"):
            return to_json_value(item.model_dump(mode="json"))
        if isinstance(item, dict):
            return {key: to_json_value(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [to_json_value(child) for child in item]
        return item

    return json.dumps(
        to_json_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def test_should_map_java_fixture_to_five_ordered_literal_search_tasks() -> None:
    tasks, state = map_public_case("java-exception-stack", remaining_tool_calls=1)

    assert [task.field_name for task in tasks] == [
        "method",
        "exception_class",
        "configuration_key",
        "error_text",
        "service_name",
    ]
    assert [(task.query, task.source_ids) for task in tasks] == [
        (
            "com.example.checkout.OrderService.placeOrder",
            ("stack-002",),
        ),
        (
            "java.lang.IllegalStateException",
            ("log-001", "stack-001"),
        ),
        ("payment.timeout", ("log-001", "stack-001")),
        (
            "Missing configuration key payment.timeout",
            ("log-001", "stack-001"),
        ),
        ("checkout-service", ("log-001",)),
    ]
    assert all(isinstance(task.arguments, SearchCodeArguments) for task in tasks)
    assert all(task.arguments.query == task.query for task in tasks)
    assert all(task.arguments.literal is True for task in tasks)
    assert all(task.arguments.repo is None for task in tasks)
    assert all(task.arguments.lang is None for task in tasks)
    assert all(task.arguments.path is None for task in tasks)
    assert state.remaining_tool_calls == 1
    assert len(tasks) == 5


def test_should_keep_all_incident_sources_traceable_through_model_input() -> None:
    incident_input, _ = validated_public_case("java-exception-stack")
    tasks, state = map_public_case("java-exception-stack")

    assert [(item.kind, item.source, item.content) for item in state.evidence] == [
        (
            EvidenceKind.FACT,
            f"incident:{source.source_type}:{source.source_id}",
            source.text,
        )
        for source in incident_input.sources
    ]
    assert json.loads(state.current_task) == {
        "task_type": "incident_retrieval",
        "tasks": [task.to_payload() for task in tasks],
    }

    model_input = ContextBuilder().build(state)

    assert model_input.current_task == state.current_task
    assert [(item.source, item.content) for item in model_input.evidence] == [
        (
            f"incident:{source.source_type}:{source.source_id}",
            source.text,
        )
        for source in incident_input.sources
    ]


def test_should_preserve_configuration_order_and_omit_missing_field_tasks() -> None:
    tasks, _ = map_public_case("multiple-missing-configuration-keys")

    assert [(task.field_name, task.query) for task in tasks] == [
        ("configuration_key", "billing.currency"),
        ("configuration_key", "billing.region"),
        ("error_text", "missing configuration keys"),
        ("service_name", "invoice-service"),
    ]
    assert all(task.query not in {"unknown", "N/A", ""} for task in tasks)


def test_should_preserve_query_whitespace_without_trimming_or_rewriting() -> None:
    incident_input, result = validated_public_case("java-exception-stack")
    exact_value_with_leading_space = SourcedIncidentValue(
        value=" Missing configuration key payment.timeout",
        source_ids=("log-001", "stack-001"),
    )
    result = result.model_copy(
        update={"error_text": exact_value_with_leading_space},
        deep=True,
    )

    tasks, _ = map_incident_to_retrieval_context(
        incident_input,
        result,
        system_instruction=SYSTEM_INSTRUCTION,
        repository_hints=(),
        selected_repository=None,
        evidence_item_budget=4,
        remaining_tool_calls=1,
    )

    error_task = next(task for task in tasks if task.field_name == "error_text")
    assert error_task.query == " Missing configuration key payment.timeout"
    assert error_task.arguments.query == error_task.query


def test_should_preserve_unicode_in_tasks_sources_state_and_model_json() -> None:
    tasks, state = map_public_case("unicode-and-missing-fields")
    model_input = ContextBuilder().build(state)

    assert [(task.field_name, task.query, task.source_ids) for task in tasks] == [
        ("error_text", "库存不足", ("log-003",)),
        ("service_name", "订单服务", ("log-003",)),
    ]
    assert state.evidence[0].content == "用户提交订单时收到失败提示。"
    assert state.evidence[1].content == "订单服务 返回错误：库存不足"
    assert "库存不足" in tasks[0].to_json()
    assert "订单服务" in state.current_task
    assert "用户提交订单时收到失败提示。" in model_input.to_json()


def test_should_leave_evidence_trimming_to_context_builder() -> None:
    incident_input, result = validated_public_case("java-exception-stack")

    _, state = map_incident_to_retrieval_context(
        incident_input,
        result,
        system_instruction=SYSTEM_INSTRUCTION,
        repository_hints=(),
        selected_repository=None,
        evidence_item_budget=1,
        remaining_tool_calls=1,
    )
    model_input = ContextBuilder().build(state)

    assert len(state.evidence) == 4
    assert [(item.source, item.content) for item in model_input.evidence] == [
        (
            "incident:description:description-001",
            "Checkout requests fail immediately after deployment.",
        )
    ]


@pytest.mark.parametrize(
    "replacement",
    [
        pytest.param(
            SourcedIncidentValue(
                value="Checkout-service",
                source_ids=("log-001",),
            ),
            id="case-drift",
        ),
        pytest.param(
            SourcedIncidentValue(
                value="checkout-service",
                source_ids=("unknown-source",),
            ),
            id="unknown-source-id",
        ),
        pytest.param(
            SourcedIncidentValue(
                value="fabricated-service",
                source_ids=("log-001",),
            ),
            id="fabricated-value",
        ),
    ],
)
def test_should_revalidate_directly_constructed_results_before_mapping(
    replacement: SourcedIncidentValue,
) -> None:
    incident_input, result = validated_public_case("java-exception-stack")
    forged_result = result.model_copy(update={"service_name": replacement}, deep=True)

    with pytest.raises(IncidentContextValidationError):
        map_incident_to_retrieval_context(
            incident_input,
            forged_result,
            system_instruction=SYSTEM_INSTRUCTION,
            repository_hints=(),
            selected_repository=None,
            evidence_item_budget=4,
            remaining_tool_calls=1,
        )


def test_should_reject_unicode_and_json_scalar_identity_drift() -> None:
    unicode_input, unicode_result = validated_public_case(
        "unicode-and-missing-fields"
    )
    unicode_forgery = unicode_result.model_copy(
        update={
            "error_text": SourcedIncidentValue(
                value="庫存不足",
                source_ids=("log-003",),
            )
        },
        deep=True,
    )
    java_input, java_result = validated_public_case("java-exception-stack")
    type_forgery = java_result.model_copy(
        update={
            "service_name": SourcedIncidentValue.model_construct(
                value=True,
                source_ids=("log-001",),
            )
        },
        deep=True,
    )

    for incident_input, forged_result in (
        (unicode_input, unicode_forgery),
        (java_input, type_forgery),
    ):
        with pytest.raises(IncidentContextValidationError):
            map_incident_to_retrieval_context(
                incident_input,
                forged_result,
                system_instruction=SYSTEM_INSTRUCTION,
                repository_hints=(),
                selected_repository=None,
                evidence_item_budget=len(incident_input.sources),
                remaining_tool_calls=1,
            )


def test_should_accept_only_an_explicit_exact_canonical_repository() -> None:
    hint = RepositoryHint(
        canonical_name="checkout-repository",
        aliases=("checkout-service", "example/checkout-repository"),
    )

    tasks, state = map_public_case(
        "java-exception-stack",
        repository_hints=[hint],
        selected_repository="checkout-repository",
    )

    assert all(task.arguments.repo == "checkout-repository" for task in tasks)
    assert state.repository_hints == (hint,)
    assert state.repository_hints[0] is not hint


@pytest.mark.parametrize(
    "selected_repository",
    [
        pytest.param("checkout-service", id="alias"),
        pytest.param("CHECKOUT-REPOSITORY", id="case-change"),
        pytest.param("unknown-repository", id="unknown"),
    ],
)
def test_should_reject_noncanonical_repository_selection(
    selected_repository: str,
) -> None:
    hint = RepositoryHint(
        canonical_name="checkout-repository",
        aliases=("checkout-service",),
    )

    with pytest.raises(IncidentContextValidationError, match="canonical"):
        map_public_case(
            "java-exception-stack",
            repository_hints=(hint,),
            selected_repository=selected_repository,
        )


def test_should_not_infer_repository_from_matching_service_name() -> None:
    tasks, _ = map_public_case(
        "java-exception-stack",
        repository_hints=(RepositoryHint(canonical_name="checkout-service"),),
        selected_repository=None,
    )

    assert all(task.arguments.repo is None for task in tasks)


def test_should_be_byte_stable_detached_and_leave_caller_objects_unchanged() -> None:
    incident_input, result = validated_public_case("java-exception-stack")
    hints = [
        RepositoryHint(
            canonical_name="checkout-repository",
            aliases=("example/checkout-repository",),
        )
    ]
    input_before = incident_input.to_json()
    result_before = result.to_json()
    hints_before = stable_json(hints)

    first_tasks, first_state = map_incident_to_retrieval_context(
        incident_input,
        result,
        system_instruction=SYSTEM_INSTRUCTION,
        repository_hints=hints,
        selected_repository="checkout-repository",
        evidence_item_budget=4,
        remaining_tool_calls=2,
    )
    second_tasks, second_state = map_incident_to_retrieval_context(
        incident_input,
        result,
        system_instruction=SYSTEM_INSTRUCTION,
        repository_hints=hints,
        selected_repository="checkout-repository",
        evidence_item_budget=4,
        remaining_tool_calls=2,
    )
    first_model_input = ContextBuilder().build(first_state)
    second_model_input = ContextBuilder().build(second_state)
    detached_task_payload = first_tasks[0].to_payload()
    detached_task_payload["source_ids"].append("mutated-copy")
    detached_task_payload["arguments"]["query"] = "mutated-copy"

    assert stable_json([task.to_payload() for task in first_tasks]) == stable_json(
        [task.to_payload() for task in second_tasks]
    )
    assert stable_json(first_state) == stable_json(second_state)
    assert first_model_input.to_json() == second_model_input.to_json()
    assert first_state.repository_hints[0] is not hints[0]
    assert first_state.repository_hints[0] is not second_state.repository_hints[0]
    assert first_tasks[0].arguments is not second_tasks[0].arguments
    assert "mutated-copy" not in first_tasks[0].to_json()
    assert incident_input.to_json() == input_before
    assert result.to_json() == result_before
    assert stable_json(hints) == hints_before


def test_should_construct_model_input_without_calling_a_model_or_tool() -> None:
    with (
        patch.object(FakeModel, "decide", autospec=True) as model_call,
        patch.object(ToolRouter, "execute", autospec=True) as tool_call,
    ):
        _, state = map_public_case("java-exception-stack")
        model_input = ContextBuilder().build(state)

    assert model_input.evidence
    model_call.assert_not_called()
    tool_call.assert_not_called()
