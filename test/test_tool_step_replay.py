import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.replay_tool_step import (
    EXPECTED_TOP_MATCH,
    ReplayFixture,
    execute_replay,
    load_fixture,
)
from src.examples.code_understanding_agent import EvidenceKind, ToolCall, ToolResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    PROJECT_ROOT
    / "evaluation"
    / "fixtures"
    / "real-model-decision-click-replay.json"
)


def _fixture_payload() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_top_level_field",
        "missing_context_field",
        "extra_decision_field",
        "missing_argument_field",
    ],
)
def test_fixture_requires_every_allowlisted_field_and_rejects_extras(
    mutation: str,
) -> None:
    payload = deepcopy(_fixture_payload())

    if mutation == "extra_top_level_field":
        payload["provider_response"] = {}
    elif mutation == "missing_context_field":
        del payload["context_state"]["evidence"]  # type: ignore[index]
    elif mutation == "extra_decision_field":
        payload["decision"]["model"] = "private-model"  # type: ignore[index]
    else:
        del payload["decision"]["arguments"]["literal"]  # type: ignore[index]

    with pytest.raises(ValidationError):
        ReplayFixture.model_validate(payload)


def test_fixture_validates_with_context_and_decision_domain_models() -> None:
    fixture = load_fixture(FIXTURE_PATH)

    assert fixture.context_state.remaining_tool_calls == 1
    assert fixture.context_state.evidence_item_budget == 4
    assert fixture.decision.call_id == "public-click-search-1"
    assert fixture.decision.arguments["repo"] == "pallets/click"


def test_replay_executes_one_resolved_call_and_emits_only_allowlisted_output() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    decision_before = fixture.decision.model_dump(mode="json")
    search_invocations: list[dict[str, object]] = []
    context_invocations = 0
    search_result = {
        "query": "def make_context",
        "duration_ms": 1,
        "matches": [EXPECTED_TOP_MATCH],
    }

    async def search_code(**arguments: object) -> dict[str, object]:
        search_invocations.append(arguments)
        return search_result

    def get_file_context(**_: object) -> dict[str, object]:
        nonlocal context_invocations
        context_invocations += 1
        return {"content": "unused"}

    execution = asyncio.run(
        execute_replay(
            fixture,
            search_code=search_code,
            get_file_context=get_file_context,
        )
    )

    assert len(search_invocations) == 1
    assert search_invocations[0] == {
        "query": "def make_context",
        "repo": "click",
        "lang": "python",
        "path": None,
        "limit": 20,
        "literal": True,
    }
    assert context_invocations == 0
    assert fixture.decision.model_dump(mode="json") == decision_before
    assert fixture.decision.arguments["repo"] == "pallets/click"

    recorded_call, recorded_result = execution.trace.events
    assert isinstance(recorded_call, ToolCall)
    assert isinstance(recorded_result, ToolResult)
    assert recorded_call.arguments["repo"] == "click"
    assert [event.event_type for event in execution.trace.events] == [
        "tool_call",
        "tool_result",
    ]
    assert execution.outcome.tool_result == recorded_result
    assert execution.outcome.next_state.remaining_tool_calls == 0
    assert execution.outcome.next_model_input.remaining_tool_calls == 0

    assert len(execution.outcome.next_model_input.evidence) == 1
    fact = execution.outcome.next_model_input.evidence[0]
    assert fact.kind is EvidenceKind.FACT
    assert fact.source == "tool_result:search_code:public-click-search-1"
    assert json.loads(fact.content) == {
        "status": "success",
        "result": search_result,
    }

    report = execution.report.model_dump(mode="json")
    assert set(report) == {
        "model_repo",
        "executed_repo",
        "trace_suffix",
        "budget",
        "tool_result",
        "top_match",
        "next_model_input",
    }
    assert set(report["budget"]) == {"before", "after"}
    assert set(report["tool_result"]) == {"status"}
    assert set(report["top_match"]) == {"repo", "path", "line", "snippet"}
    assert set(report["next_model_input"]) == {
        "remaining_tool_calls",
        "new_fact_evidence",
    }
    assert set(report["next_model_input"]["new_fact_evidence"]) == {
        "kind",
        "source",
        "content",
    }
    assert report["model_repo"] == "pallets/click"
    assert report["executed_repo"] == "click"
    assert report["trace_suffix"] == ["tool_call", "tool_result"]
    assert report["budget"] == {"before": 1, "after": 0}
    assert report["tool_result"] == {"status": "success"}
    assert report["top_match"] == EXPECTED_TOP_MATCH
    assert report["next_model_input"] == {
        "remaining_tool_calls": 0,
        "new_fact_evidence": fact.model_dump(mode="json"),
    }
