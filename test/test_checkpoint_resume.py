import asyncio
import json
import os
import subprocess
import sys
from copy import deepcopy
from itertools import count
from pathlib import Path

import pytest

from src.examples.code_understanding_agent import (
    AgentLoop,
    CheckpointContractError,
    CheckpointResumeError,
    CompletedCheckpoint,
    ContextBuilder,
    ContextState,
    FakeModel,
    FileCheckpointStore,
    FinalAnswer,
    FinalAnswerCitation,
    FinalAnswerDecision,
    GET_FILE_CONTEXT,
    GetFileContextArguments,
    ModelInput,
    PydanticToolAdapter,
    ResumableCheckpoint,
    SEARCH_CODE,
    SearchCodeArguments,
    Session,
    Step,
    ToolCall,
    ToolCallDecision,
    ToolResult,
    ToolRouter,
    ToolStepExecutor,
    TraceRecorder,
    TracedModelClient,
    checkpoint_from_dict,
)


TASK = "Explain how Click DateTime tries formats and renders total failure."
RESULT = {
    "matches": [
        {
            "repo": "click",
            "path": "src/click/types.py",
            "line": 491,
            "snippet": "formats = map(repr, self.formats)",
        }
    ]
}
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SimulatedProcessInterruption(RuntimeError):
    pass


def build_loop(
    *,
    trace: TraceRecorder,
    decisions,
    store: FileCheckpointStore,
    search_handler,
    on_checkpoint_saved=None,
):
    builder = ContextBuilder()
    fake_model = FakeModel(decisions)
    request_numbers = count(1)
    traced_model = TracedModelClient(
        client=fake_model,
        model="offline-checkpoint-model",
        trace=trace,
        request_id_factory=lambda: f"request-{next(request_numbers)}",
        clock=lambda: 0.0,
    )

    def get_file_context(**_: object) -> dict[str, object]:
        return {"content": "unused"}

    router = ToolRouter(
        trace=trace,
        tools={
            SEARCH_CODE: PydanticToolAdapter(
                SearchCodeArguments,
                search_handler,
            ),
            GET_FILE_CONTEXT: PydanticToolAdapter(
                GetFileContextArguments,
                get_file_context,
            ),
        },
    )
    loop = AgentLoop(
        context_builder=builder,
        model=traced_model,
        tool_step_executor=ToolStepExecutor(
            router=router,
            context_builder=builder,
        ),
        trace=trace,
        checkpoint_store=store,
        on_checkpoint_saved=on_checkpoint_saved,
    )
    return loop, fake_model


def write_process_a_checkpoint(tmp_path):
    path = tmp_path / "agent-checkpoint.json"
    store = FileCheckpointStore(path)
    trace = TraceRecorder(task_id="resume-datetime-task")
    tool_invocations = 0

    def search_code(**_: object) -> dict[str, object]:
        nonlocal tool_invocations
        tool_invocations += 1
        return RESULT

    def interrupt_after_durable_save(_: ResumableCheckpoint) -> None:
        raise SimulatedProcessInterruption("simulated process exit")

    loop, model = build_loop(
        trace=trace,
        decisions=(
            ToolCallDecision(
                call_id="call-datetime",
                tool_name=SEARCH_CODE,
                arguments={"query": "DateTime convert formats", "repo": "click"},
            ),
        ),
        store=store,
        search_handler=search_code,
        on_checkpoint_saved=interrupt_after_durable_save,
    )
    state = ContextState(
        system_instruction="Answer only from verified Click source evidence.",
        current_task=TASK,
        evidence_item_budget=0,
        remaining_tool_calls=1,
    )

    with pytest.raises(SimulatedProcessInterruption, match="simulated process exit"):
        asyncio.run(loop.run(state))

    record = store.load()
    assert isinstance(record, ResumableCheckpoint)
    assert tool_invocations == 1
    assert len(model.model_inputs) == 1
    return store, record, trace


def test_new_loop_resumes_exact_input_without_repeating_completed_tool(tmp_path) -> None:
    store, checkpoint, process_a_trace = write_process_a_checkpoint(tmp_path)
    checkpoint_json_before_resume = checkpoint.to_json()

    assert checkpoint.remaining_tool_calls == 0
    assert checkpoint.next_model_input.remaining_tool_calls == 0
    assert len(checkpoint.next_model_input.evidence) == 1
    assert "call-datetime" in checkpoint.next_model_input.evidence[0].source
    assert [event.event_type for event in process_a_trace.events] == [
        "session",
        "step",
        "model_request",
        "model_result",
        "tool_call",
        "tool_result",
    ]

    process_b_trace = checkpoint.restore_trace()
    resumed_tool_invocations = 0

    def must_not_repeat_search(**_: object) -> dict[str, object]:
        nonlocal resumed_tool_invocations
        resumed_tool_invocations += 1
        return RESULT

    process_b_loop, process_b_model = build_loop(
        trace=process_b_trace,
        decisions=(
            FinalAnswerDecision(
                answer=(
                    "Click tries each configured DateTime format in order and "
                    "renders a singular or plural failure after all formats fail."
                ),
                evidence=(
                    FinalAnswerCitation(
                        repo="click",
                        path="src/click/types.py",
                        line=491,
                    ),
                ),
            ),
        ),
        store=store,
        search_handler=must_not_repeat_search,
    )

    outcome = asyncio.run(process_b_loop.resume(checkpoint))

    assert resumed_tool_invocations == 0
    assert process_b_model.model_inputs == (checkpoint.next_model_input,)
    assert checkpoint.to_json() == checkpoint_json_before_resume
    assert outcome.tool_calls_used == 1
    assert outcome.final_answer.termination_reason == "completed"
    assert outcome.final_answer.evidence == ["click/src/click/types.py:491"]

    events = process_b_trace.events
    assert [event.event_type for event in events] == [
        "session",
        "step",
        "model_request",
        "model_result",
        "tool_call",
        "tool_result",
        "step",
        "model_request",
        "model_result",
        "final_answer",
    ]
    assert len([event for event in events if isinstance(event, Session)]) == 1
    assert len([event for event in events if isinstance(event, ToolCall)]) == 1
    assert len([event for event in events if isinstance(event, ToolResult)]) == 1
    assert len([event for event in events if isinstance(event, FinalAnswer)]) == 1
    assert [event.step_number for event in events if isinstance(event, Step)] == [1, 2]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))

    completed = store.load()
    assert isinstance(completed, CompletedCheckpoint)
    assert completed.generation == checkpoint.generation + 1
    assert completed.trace == process_b_trace.to_dict()

    guard_trace = completed.restore_trace()
    guard_calls = 0

    def guard_search(**_: object) -> dict[str, object]:
        nonlocal guard_calls
        guard_calls += 1
        return RESULT

    guard_loop, guard_model = build_loop(
        trace=guard_trace,
        decisions=(FinalAnswerDecision(answer="must not run"),),
        store=store,
        search_handler=guard_search,
    )
    with pytest.raises(
        CheckpointResumeError,
        match="completed checkpoint cannot be resumed",
    ):
        asyncio.run(guard_loop.resume(completed))
    assert guard_calls == 0
    assert guard_model.model_inputs == ()

    cached_trace = checkpoint.restore_trace()
    cached_loop, cached_model = build_loop(
        trace=cached_trace,
        decisions=(FinalAnswerDecision(answer="must not run"),),
        store=store,
        search_handler=guard_search,
    )
    with pytest.raises(
        CheckpointResumeError,
        match="completed checkpoint cannot be resumed",
    ):
        asyncio.run(cached_loop.resume(checkpoint))
    assert guard_calls == 0
    assert cached_model.model_inputs == ()
    assert cached_trace.to_dict() == checkpoint.trace


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda payload: payload.update({"task_id": "different-task"}),
            id="task-id-mismatch",
        ),
        pytest.param(
            lambda payload: payload["trace"]["events"][-1].update(
                {"sequence": 999}
            ),
            id="sequence-gap",
        ),
        pytest.param(
            lambda payload: payload.update({"remaining_tool_calls": 1}),
            id="budget-mismatch",
        ),
        pytest.param(
            lambda payload: payload.update({"phase": "before_model_request"}),
            id="wrong-phase",
        ),
        pytest.param(
            lambda payload: payload["trace"]["events"].pop(),
            id="dangling-tool-call",
        ),
        pytest.param(
            lambda payload: payload["trace"]["events"].__delitem__(
                slice(-3, None)
            ),
            id="dangling-model-request",
        ),
        pytest.param(
            lambda payload: payload["trace"]["events"][-1].update(
                {
                    "status": "error",
                    "result": None,
                    "error_type": "tool_execution_error",
                }
            ),
            id="error-tool-result",
        ),
        pytest.param(
            lambda payload: payload["trace"].update({"finalized": True}),
            id="finalized-resumable",
        ),
        pytest.param(
            lambda payload: payload["next_model_input"].update({"evidence": []}),
            id="lost-protected-fact",
        ),
    ],
)
def test_illegal_checkpoint_contracts_fail_closed(mutate, tmp_path) -> None:
    _, checkpoint, _ = write_process_a_checkpoint(tmp_path)
    payload = deepcopy(checkpoint.to_payload())
    mutate(payload)

    with pytest.raises(CheckpointContractError):
        checkpoint_from_dict(payload)


class SpyModel:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, _: ModelInput) -> object:
        self.calls += 1
        return FinalAnswerDecision(answer="must not run")


class SpyRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, _: ToolCall) -> ToolResult:
        self.calls += 1
        return ToolResult(call_id="must-not-run", status="success", result={})


def test_resume_rejects_mismatched_loop_trace_before_model_or_tool(tmp_path) -> None:
    store, checkpoint, _ = write_process_a_checkpoint(tmp_path)
    wrong_trace = TraceRecorder(task_id=checkpoint.task_id)
    model = SpyModel()
    router = SpyRouter()
    loop = AgentLoop(
        context_builder=ContextBuilder(),
        model=model,
        tool_step_executor=ToolStepExecutor(router=router),
        trace=wrong_trace,
        checkpoint_store=store,
    )

    with pytest.raises(CheckpointResumeError, match="loop trace does not match"):
        asyncio.run(loop.resume(checkpoint))

    assert model.calls == 0
    assert router.calls == 0
    assert wrong_trace.events == ()

    fresh_trace = TraceRecorder(task_id="fresh-task-must-not-overwrite")
    fresh_model = SpyModel()
    fresh_router = SpyRouter()
    fresh_loop = AgentLoop(
        context_builder=ContextBuilder(),
        model=fresh_model,
        tool_step_executor=ToolStepExecutor(router=fresh_router),
        trace=fresh_trace,
        checkpoint_store=store,
    )
    with pytest.raises(
        CheckpointResumeError,
        match="checkpoint store already contains a record",
    ):
        asyncio.run(
            fresh_loop.run(
                ContextState(
                    system_instruction="Use verified evidence.",
                    current_task="This new task must not overwrite old state.",
                    evidence_item_budget=0,
                    remaining_tool_calls=1,
                )
            )
        )
    assert fresh_model.calls == 0
    assert fresh_router.calls == 0
    assert fresh_trace.events == ()
    assert store.load() == checkpoint


def test_resume_rejects_stale_generation_before_model_or_tool(tmp_path) -> None:
    store, checkpoint, _ = write_process_a_checkpoint(tmp_path)
    stale = checkpoint.model_copy(update={"generation": 2}, deep=True)
    trace = checkpoint.restore_trace()
    model = SpyModel()
    router = SpyRouter()
    loop = AgentLoop(
        context_builder=ContextBuilder(),
        model=model,
        tool_step_executor=ToolStepExecutor(router=router),
        trace=trace,
        checkpoint_store=store,
    )

    with pytest.raises(CheckpointResumeError, match="stale or was replaced"):
        asyncio.run(loop.resume(stale))

    assert model.calls == 0
    assert router.calls == 0
    assert trace.to_dict() == checkpoint.trace


def test_each_successful_tool_advances_generation_before_terminal_record(
    tmp_path,
) -> None:
    store = FileCheckpointStore(tmp_path / "agent-checkpoint.json")
    trace = TraceRecorder(task_id="multi-generation-task")
    saved: list[ResumableCheckpoint] = []
    tool_invocations = 0

    def search_code(**_: object) -> dict[str, object]:
        nonlocal tool_invocations
        tool_invocations += 1
        return RESULT

    loop, model = build_loop(
        trace=trace,
        decisions=(
            ToolCallDecision(
                call_id="call-first",
                tool_name=SEARCH_CODE,
                arguments={"query": "DateTime convert", "repo": "click"},
            ),
            ToolCallDecision(
                call_id="call-second",
                tool_name=SEARCH_CODE,
                arguments={"query": "DateTime formats", "repo": "click"},
            ),
            FinalAnswerDecision(
                answer="Click tries every configured format before failing.",
                evidence=(
                    FinalAnswerCitation(
                        repo="click",
                        path="src/click/types.py",
                        line=491,
                    ),
                ),
            ),
        ),
        store=store,
        search_handler=search_code,
        on_checkpoint_saved=lambda checkpoint: saved.append(checkpoint),
    )
    state = ContextState(
        system_instruction="Answer only from verified Click source evidence.",
        current_task=TASK,
        evidence_item_budget=1,
        remaining_tool_calls=2,
    )

    outcome = asyncio.run(loop.run(state))

    assert [checkpoint.generation for checkpoint in saved] == [1, 2]
    assert saved[1].trace["events"][: len(saved[0].trace["events"])] == saved[
        0
    ].trace["events"]
    assert tool_invocations == 2
    assert len(model.model_inputs) == 3
    assert outcome.tool_calls_used == 2
    completed = store.load()
    assert isinstance(completed, CompletedCheckpoint)
    assert completed.generation == 3


def test_offline_demo_uses_two_processes_and_emits_sanitized_evidence(
    tmp_path,
) -> None:
    report_path = tmp_path / "checkpoint-resume.json"
    summary_path = tmp_path / "checkpoint-resume.md"
    environment = dict(os.environ)
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "."})

    result = subprocess.run(
        [
            sys.executable,
            "evaluation/run_checkpoint_resume_demo.py",
            "run",
            "--report-out",
            str(report_path),
            "--summary-out",
            str(summary_path),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    stable_output = json.loads(result.stdout)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = summary_path.read_text(encoding="utf-8")
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert stable_output["status"] == "passed"
    assert stable_output["tool_execution_count"] == 1
    assert report["processes"] == {
        "a": {
            "exit_code": 75,
            "status": "checkpoint_saved_then_interrupted",
        },
        "b": {"exit_code": 0, "status": "completed"},
    }
    assert report["proof"]["tool_execution_count_across_processes"] == 1
    assert report["proof"]["process_b_received_exact_saved_input"] is True
    assert report["trace"]["session_count"] == 1
    assert report["trace"]["tool_result_count"] == 1
    assert report["trace"]["terminal_count"] == 1
    assert report["trace"]["after_resume_sequences"] == list(range(1, 11))
    assert report["scenario"]["counts_as_m4_final_incident_case"] is False
    assert len(report["checkpoint"]["resumable_sha256"]) == 64
    assert len(report["checkpoint"]["completed_sha256"]) == 64
    assert str(tmp_path) not in serialized
    assert str(PROJECT_ROOT) not in serialized
    assert "Tool executions across both processes: `1`" in summary
