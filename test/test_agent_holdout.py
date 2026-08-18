import json

import pytest

from evaluation.run_agent_eval import AnswerableExpected, GoldLocation
from evaluation.run_agent_holdout import (
    DEFAULT_EXCLUSION_SET_PATH,
    DEFAULT_HOLDOUT_CASES_PATH,
    EXPECTED_EXCLUSION_COUNTS,
    FROZEN_EXCLUSION_SET_SHA256,
    FROZEN_HOLDOUT_DATASET_SHA256,
    AgentHoldoutDataError,
    main,
    validate_frozen_holdout_dataset,
    validate_holdout_disjointness,
)


def test_frozen_holdout_validates_distribution_revision_and_exclusions() -> None:
    cases, digest, exclusion_set = validate_frozen_holdout_dataset()

    assert len(cases) == 10
    assert digest == FROZEN_HOLDOUT_DATASET_SHA256
    assert sum(case.expected.result == "answerable" for case in cases) == 8
    assert sum(case.expected.result == "insufficient_evidence" for case in cases) == 2
    assert len({case.id for case in cases}) == 10
    assert len({case.question for case in cases}) == 10
    assert {
        source["kind"]: source["case_count"]
        for source in exclusion_set["sources"]
    } == EXPECTED_EXCLUSION_COUNTS


def test_holdout_rejects_gold_that_overlaps_an_excluded_target() -> None:
    cases, _, exclusion_set = validate_frozen_holdout_dataset()
    overlapping = cases[0].model_copy(
        update={
            "expected": AnswerableExpected(
                result="answerable",
                locations=(
                    GoldLocation(
                        repo="click",
                        path="src/click/utils.py",
                        line_min=234,
                        line_max=234,
                    ),
                ),
                review_note="Deliberately overlaps the old echo gold.",
            )
        }
    )

    with pytest.raises(AgentHoldoutDataError, match="gold location overlaps"):
        validate_holdout_disjointness((overlapping, *cases[1:]), exclusion_set)


def test_validate_only_reports_frozen_contract_without_model_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)

    assert main(["--validate-only"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "answerable_cases": 8,
        "case_count": 10,
        "click_revision": "6eeb50e948ea136db145280f6f5dd52eca3fa7e5",
        "dataset_sha256": FROZEN_HOLDOUT_DATASET_SHA256,
        "exclusion_counts": EXPECTED_EXCLUSION_COUNTS,
        "exclusion_set_sha256": FROZEN_EXCLUSION_SET_SHA256,
        "insufficient_evidence_cases": 2,
        "status": "valid",
    }


def test_default_holdout_artifacts_have_independent_names() -> None:
    assert DEFAULT_HOLDOUT_CASES_PATH.name == "agent_holdout_cases_2026-08-19.jsonl"
    assert (
        DEFAULT_EXCLUSION_SET_PATH.name
        == "agent-holdout-exclusion-set-2026-08-19.json"
    )
