import json
import stat
from copy import deepcopy

import pytest

import src.examples.code_understanding_agent.checkpoint as checkpoint_module
from src.examples.code_understanding_agent import (
    CheckpointContractError,
    CheckpointStoreError,
    CompletedCheckpoint,
    ContextBuilder,
    ContextState,
    Evidence,
    EvidenceKind,
    FileCheckpointStore,
    FinalAnswer,
    ModelRequest,
    ModelResult,
    RESUMABLE_PHASE,
    ResumableCheckpoint,
    SEARCH_CODE,
    Session,
    Step,
    ToolCall,
    ToolCallDecision,
    ToolResult,
    TraceRecorder,
    checkpoint_from_dict,
    checkpoint_from_json,
)


def build_resumable_checkpoint() -> ResumableCheckpoint:
    builder = ContextBuilder()
    initial_state = ContextState(
        system_instruction="Answer only from verified Click source evidence.",
        current_task=(
            "Explain how Click DateTime tries formats and renders total failure."
        ),
        evidence_item_budget=0,
        remaining_tool_calls=1,
    )
    initial_input = builder.build(initial_state)
    decision = ToolCallDecision(
        call_id="call-datetime",
        tool_name=SEARCH_CODE,
        arguments={"query": "DateTime convert formats", "repo": "click"},
    )
    result_payload = {
        "matches": [
            {
                "repo": "click",
                "path": "src/click/types.py",
                "line": 491,
                "snippet": "formats = map(repr, self.formats)",
            }
        ]
    }
    fact = Evidence(
        kind=EvidenceKind.FACT,
        source="tool_result:search_code:call-datetime",
        content=json.dumps(
            {"result": result_payload, "status": "success"},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
    state = initial_state.model_copy(
        update={"evidence": (fact,), "remaining_tool_calls": 0},
        deep=True,
    )
    next_input = builder.build(state, protected_evidence=(fact,))

    trace = TraceRecorder(task_id="checkpoint-datetime-task")
    trace.append(
        Session(
            user_task=initial_state.current_task,
            available_tools=["search_code", "get_file_context"],
        )
    )
    trace.append(Step(step_number=1, purpose="Request the next model decision."))
    trace.append(
        ModelRequest(
            request_id="request-search",
            model="fake-agent-model",
            model_input=initial_input.to_payload(),
        )
    )
    trace.append(
        ModelResult(
            request_id="request-search",
            status="success",
            decision=decision.model_dump(mode="json"),
            elapsed_ms=0,
        )
    )
    trace.append(
        ToolCall(
            call_id=decision.call_id,
            tool_name=decision.tool_name,
            arguments=decision.arguments,
        )
    )
    trace.append(
        ToolResult(
            call_id=decision.call_id,
            status="success",
            result=result_payload,
        )
    )

    return ResumableCheckpoint(
        task_id=trace.task_id,
        generation=1,
        phase=RESUMABLE_PHASE,
        state=state,
        next_model_input=next_input,
        tool_calls_used=1,
        remaining_tool_calls=0,
        next_step_number=2,
        trace=trace.to_dict(),
    )


def test_checkpoint_json_round_trip_is_stable_and_detached() -> None:
    checkpoint = build_resumable_checkpoint()
    payload = checkpoint.to_payload()
    original_payload = deepcopy(payload)

    restored = checkpoint_from_json(checkpoint.to_json())

    assert restored == checkpoint
    assert restored.to_json() == checkpoint.to_json()
    assert payload == original_payload
    payload["state"]["remaining_tool_calls"] = 99
    payload["trace"]["events"][0]["user_task"] = "mutated"
    assert checkpoint.to_payload() == original_payload
    assert checkpoint.next_model_input.evidence == checkpoint.state.evidence


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda payload: payload.update({"schema_version": 999}),
            id="unknown-version",
        ),
        pytest.param(
            lambda payload: payload.update({"unknown_field": True}),
            id="unknown-top-level-field",
        ),
        pytest.param(
            lambda payload: payload["trace"].update({"unknown_field": True}),
            id="unknown-trace-field",
        ),
        pytest.param(
            lambda payload: payload["trace"]["events"][0].update(
                {"unknown_field": True}
            ),
            id="unknown-event-field",
        ),
        pytest.param(
            lambda payload: payload.update({"generation": "1"}),
            id="coerced-generation",
        ),
    ],
)
def test_checkpoint_contract_rejects_unknown_or_non_strict_input(mutate) -> None:
    payload = build_resumable_checkpoint().to_payload()
    mutate(payload)

    with pytest.raises(
        CheckpointContractError,
        match="checkpoint contract validation failed",
    ):
        checkpoint_from_dict(payload)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{", id="truncated"),
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"value":NaN}', id="non-finite-number"),
    ],
)
def test_checkpoint_json_rejects_corruption(payload: str) -> None:
    with pytest.raises(CheckpointContractError):
        checkpoint_from_json(payload)


def test_checkpoint_json_rejects_duplicate_keys() -> None:
    payload = build_resumable_checkpoint().to_json().replace(
        '"schema_version":1',
        '"schema_version":1,"schema_version":1',
        1,
    )

    with pytest.raises(CheckpointContractError, match="checkpoint JSON is invalid"):
        checkpoint_from_json(payload)


def test_file_store_uses_private_atomic_file_and_preserves_previous_on_replace_error(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "agent-checkpoint.json"
    store = FileCheckpointStore(path)
    first = build_resumable_checkpoint()
    store.save(first)
    original_bytes = path.read_bytes()

    assert store.load() == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    completed_trace = first.restore_trace()
    completed_trace.append(
        Step(step_number=2, purpose="Request the next model decision.")
    )
    completed_trace.append(
        ModelRequest(
            request_id="request-final",
            model="fake-agent-model",
            model_input=first.next_model_input.to_payload(),
        )
    )
    completed_trace.append(
        ModelResult(
            request_id="request-final",
            status="success",
            decision={
                "decision_type": "final_answer",
                "answer": "Click tries each format before rendering failure.",
                "evidence": ["click/src/click/types.py:491"],
                "uncertainties": [],
                "next_queries": [],
            },
            elapsed_ms=0,
        )
    )
    completed_trace.finalize(
        FinalAnswer(
            answer="Click tries each format before rendering failure.",
            evidence=["click/src/click/types.py:491"],
            termination_reason="completed",
        )
    )
    second = CompletedCheckpoint(
        task_id=first.task_id,
        generation=2,
        final_state=first.state,
        tool_calls_used=1,
        remaining_tool_calls=0,
        trace=completed_trace.to_dict(),
    )

    def fail_replace(*_: object) -> None:
        raise OSError("simulated replace interruption")

    monkeypatch.setattr(checkpoint_module.os, "replace", fail_replace)
    with pytest.raises(CheckpointStoreError, match="atomic write failed"):
        store.save(second)

    assert path.read_bytes() == original_bytes
    assert store.load() == first
    assert list(tmp_path.glob(".agent-checkpoint.json.*.tmp")) == []


def test_file_store_fails_closed_on_corrupt_current_file(tmp_path) -> None:
    path = tmp_path / "agent-checkpoint.json"
    path.write_text('{"schema_version":', encoding="utf-8")
    store = FileCheckpointStore(path)

    with pytest.raises(CheckpointStoreError, match="checkpoint file is invalid"):
        store.load()
    with pytest.raises(CheckpointStoreError, match="checkpoint file is invalid"):
        store.save(build_resumable_checkpoint())
