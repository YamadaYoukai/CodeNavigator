import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.examples.code_understanding_agent import (
    FakeIncidentFieldExtractor,
    IncidentExtractionCandidate,
    IncidentExtractionResult,
    IncidentExtractionValidationError,
    IncidentInput,
    IncidentSource,
    SourcedIncidentValue,
    extract_incident_fields,
    validate_incident_extraction,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "evaluation"
    / "fixtures"
    / "incident-extraction-public-cases-2026-08-26.json"
)


def load_public_cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def parse_input(payload: dict[str, Any]) -> IncidentInput:
    return IncidentInput.model_validate_json(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        strict=True,
    )


def parse_candidate(payload: dict[str, Any]) -> IncidentExtractionCandidate:
    return IncidentExtractionCandidate.model_validate_json(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        strict=True,
    )


def build_incident_input() -> IncidentInput:
    return IncidentInput(
        sources=(
            IncidentSource(
                source_id="log-001",
                source_type="log",
                text=(
                    "checkout-service failed with "
                    "java.lang.IllegalStateException: payment.timeout is missing"
                ),
            ),
            IncidentSource(
                source_id="stack-001",
                source_type="stack_trace",
                text="    at example.Checkout.submit(Checkout.java:42)",
            ),
        )
    )


def sourced(value: str, *source_ids: str) -> SourcedIncidentValue:
    return SourcedIncidentValue(value=value, source_ids=source_ids)


def build_candidate() -> IncidentExtractionCandidate:
    return IncidentExtractionCandidate(
        exception_class=sourced(
            "java.lang.IllegalStateException",
            "log-001",
        ),
        method=sourced("example.Checkout.submit", "stack-001"),
        error_text=sourced("payment.timeout is missing", "log-001"),
        service_name=sourced("checkout-service", "log-001"),
        configuration_keys=(sourced("payment.timeout", "log-001"),),
    )


@pytest.mark.parametrize("case", load_public_cases(), ids=lambda case: case["case_id"])
def test_should_validate_each_public_offline_case(case: dict[str, Any]) -> None:
    incident_input = parse_input(case["input"])
    candidate = parse_candidate(case["candidate"])

    result = validate_incident_extraction(incident_input, candidate)

    assert isinstance(result, IncidentExtractionResult)
    assert result.to_payload() == case["candidate"]
    assert result.to_json() == result.to_json()


def test_should_preserve_source_and_configuration_order_and_unicode() -> None:
    case = load_public_cases()[2]
    incident_input = parse_input(case["input"])
    candidate = parse_candidate(case["candidate"])

    result = validate_incident_extraction(incident_input, candidate)
    restored_input = IncidentInput.model_validate_json(incident_input.to_json())
    restored_result = IncidentExtractionResult.model_validate_json(result.to_json())

    assert [source.source_id for source in restored_input.sources] == [
        "description-003",
        "log-003",
    ]
    assert restored_input.sources[0].text == "用户提交订单时收到失败提示。"
    assert restored_result.error_text is not None
    assert restored_result.error_text.value == "库存不足"
    assert "用户提交订单时收到失败提示。" in incident_input.to_json()
    assert "库存不足" in result.to_json()


def test_should_expose_only_the_five_frozen_output_fields_in_stable_order() -> None:
    result = validate_incident_extraction(build_incident_input(), build_candidate())

    assert list(result.to_payload()) == [
        "exception_class",
        "method",
        "error_text",
        "service_name",
        "configuration_keys",
    ]
    schema = IncidentExtractionResult.model_json_schema()
    assert schema["additionalProperties"] is False
    assert list(schema["properties"]) == [
        "exception_class",
        "method",
        "error_text",
        "service_name",
        "configuration_keys",
    ]
    assert set(schema["required"]) == set(schema["properties"])


def test_should_make_missing_fields_explicit_without_guessing() -> None:
    candidate = IncidentExtractionCandidate(
        exception_class=None,
        method=None,
        error_text=sourced("payment.timeout is missing", "log-001"),
        service_name=None,
        configuration_keys=(),
    )

    result = validate_incident_extraction(build_incident_input(), candidate)

    assert result.exception_class is None
    assert result.method is None
    assert result.service_name is None
    assert result.configuration_keys == ()
    assert "unknown" not in result.to_json().lower()
    assert "n/a" not in result.to_json().lower()


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"sources": ()}, id="no-source-lines"),
        pytest.param(
            {
                "sources": (
                    {
                        "source_id": "log-001",
                        "source_type": "log",
                        "text": "valid text",
                        "extra": "forbidden",
                    },
                )
            },
            id="extra-source-field",
        ),
        pytest.param(
            {
                "sources": (
                    {
                        "source_id": "log-001",
                        "source_type": "metric",
                        "text": "valid text",
                    },
                )
            },
            id="unknown-source-type",
        ),
        pytest.param(
            {
                "sources": (
                    {
                        "source_id": " log-001",
                        "source_type": "log",
                        "text": "valid text",
                    },
                )
            },
            id="source-id-surrounding-whitespace",
        ),
        pytest.param(
            {
                "sources": (
                    {
                        "source_id": "log-001",
                        "source_type": "log",
                        "text": " \t ",
                    },
                )
            },
            id="whitespace-only-source-text",
        ),
        pytest.param(
            {
                "sources": (
                    {
                        "source_id": "log-001",
                        "source_type": "log",
                        "text": "first line\nsecond line",
                    },
                )
            },
            id="more-than-one-source-line",
        ),
        pytest.param(
            {
                "sources": (
                    {
                        "source_id": True,
                        "source_type": "log",
                        "text": "valid text",
                    },
                )
            },
            id="boolean-source-id",
        ),
        pytest.param(
            {
                "sources": (
                    {
                        "source_id": "log-001",
                        "source_type": "log",
                        "text": 3.0,
                    },
                )
            },
            id="float-source-text",
        ),
        pytest.param(
            {
                "sources": [
                    {
                        "source_id": "log-001",
                        "source_type": "log",
                        "text": "valid text",
                    }
                ]
            },
            id="python-list-container",
        ),
        pytest.param(
            {
                "sources": (
                    {
                        "source_id": "log-001",
                        "source_type": "log",
                        "text": "valid text",
                    },
                ),
                "extra": True,
            },
            id="extra-input-field",
        ),
    ],
)
def test_should_reject_invalid_incident_input_contract(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        IncidentInput.model_validate(payload, strict=True)


def test_should_reject_duplicate_source_ids() -> None:
    source = IncidentSource(
        source_id="log-001",
        source_type="log",
        text="valid text",
    )

    with pytest.raises(ValidationError, match="source_id values must be unique"):
        IncidentInput(sources=(source, source.model_copy(deep=True)))


def valid_candidate_payload() -> dict[str, object]:
    return build_candidate().model_dump(mode="python")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param(
            "exception_class",
            {"value": "java.lang.IllegalStateException", "source_ids": ()},
            id="empty-source-id-list",
        ),
        pytest.param(
            "method",
            {"value": " ", "source_ids": ("stack-001",)},
            id="whitespace-only-observed-value",
        ),
        pytest.param(
            "error_text",
            {"value": True, "source_ids": ("log-001",)},
            id="boolean-value-is-not-integer-or-text",
        ),
        pytest.param(
            "error_text",
            {"value": 3, "source_ids": ("log-001",)},
            id="integer-value-is-not-text",
        ),
        pytest.param(
            "error_text",
            {"value": 3.0, "source_ids": ("log-001",)},
            id="equal-float-value-is-not-integer-or-text",
        ),
        pytest.param(
            "service_name",
            {
                "value": "checkout-service",
                "source_ids": ("log-001",),
                "confidence": 1.0,
            },
            id="extra-observation-field",
        ),
        pytest.param(
            "configuration_keys",
            [
                {
                    "value": "payment.timeout",
                    "source_ids": ("log-001",),
                }
            ],
            id="python-list-configuration-container",
        ),
        pytest.param(
            "method",
            {
                "value": "example.Checkout.submit",
                "source_ids": ["stack-001"],
            },
            id="python-list-source-id-container",
        ),
        pytest.param(
            "configuration_keys",
            None,
            id="configuration-missing-must-be-empty-tuple",
        ),
    ],
)
def test_should_reject_invalid_structured_candidate_values(
    field: str,
    value: object,
) -> None:
    payload = valid_candidate_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        IncidentExtractionCandidate.model_validate(payload, strict=True)


def test_should_reject_missing_or_additional_output_categories() -> None:
    missing = valid_candidate_payload()
    del missing["method"]
    additional = valid_candidate_payload()
    additional["root_cause"] = "not observable"

    with pytest.raises(ValidationError):
        IncidentExtractionCandidate.model_validate(missing, strict=True)
    with pytest.raises(ValidationError):
        IncidentExtractionCandidate.model_validate(additional, strict=True)


def test_should_reject_duplicate_source_references_and_configuration_keys() -> None:
    with pytest.raises(ValidationError, match="source_ids must be unique"):
        SourcedIncidentValue(
            value="payment.timeout",
            source_ids=("log-001", "log-001"),
        )

    with pytest.raises(ValidationError, match="configuration keys must be unique"):
        IncidentExtractionCandidate(
            exception_class=None,
            method=None,
            error_text=None,
            service_name=None,
            configuration_keys=(
                sourced("payment.timeout", "log-001"),
                sourced("payment.timeout", "log-001"),
            ),
        )


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        pytest.param(
            sourced("guessed-service", "log-001"),
            "exact source text",
            id="fabricated-value",
        ),
        pytest.param(
            sourced("Checkout-Service", "log-001"),
            "exact source text",
            id="case-insensitive-match-is-forbidden",
        ),
        pytest.param(
            sourced("checkout-service", "missing-source"),
            "unknown source_id",
            id="unknown-source-id",
        ),
    ],
)
def test_should_fail_closed_when_service_provenance_is_invalid(
    replacement: SourcedIncidentValue,
    message: str,
) -> None:
    payload = valid_candidate_payload()
    payload["service_name"] = replacement
    candidate = IncidentExtractionCandidate.model_validate(payload, strict=True)

    with pytest.raises(IncidentExtractionValidationError, match=message):
        validate_incident_extraction(build_incident_input(), candidate)


def test_should_require_exact_support_from_every_referenced_source_line() -> None:
    payload = valid_candidate_payload()
    payload["service_name"] = sourced(
        "checkout-service",
        "log-001",
        "stack-001",
    )
    candidate = IncidentExtractionCandidate.model_validate(payload, strict=True)

    with pytest.raises(
        IncidentExtractionValidationError,
        match="exact source text",
    ):
        validate_incident_extraction(build_incident_input(), candidate)


def test_should_keep_fake_scripts_inputs_and_results_detached() -> None:
    incident_input = build_incident_input()
    candidate = build_candidate()
    input_before = incident_input.to_json()
    candidate_before = candidate.to_json()
    fake = FakeIncidentFieldExtractor((candidate,))

    result = extract_incident_fields(fake, incident_input)
    payload = result.to_payload()
    payload["configuration_keys"][0]["source_ids"].append("mutated-copy")
    recorded = fake.incident_inputs

    assert result == validate_incident_extraction(incident_input, candidate)
    assert incident_input.to_json() == input_before
    assert candidate.to_json() == candidate_before
    assert recorded == (incident_input,)
    assert recorded[0] is not incident_input
    assert fake.incident_inputs[0] is not recorded[0]
    assert fake.remaining_candidates == 0
    assert "mutated-copy" not in result.to_json()


def test_should_fail_before_validation_when_extractor_returns_wrong_type() -> None:
    class InvalidExtractor:
        def extract(self, incident_input: IncidentInput) -> object:
            return {"service_name": "checkout-service"}

    with pytest.raises(TypeError, match="IncidentExtractionCandidate"):
        extract_incident_fields(InvalidExtractor(), build_incident_input())


def test_should_reject_invalid_fake_scripts_and_exhaustion() -> None:
    with pytest.raises(TypeError, match="IncidentExtractionCandidate"):
        FakeIncidentFieldExtractor((build_candidate(), object()))

    fake = FakeIncidentFieldExtractor(())
    with pytest.raises(RuntimeError, match="no scripted candidates"):
        fake.extract(build_incident_input())
