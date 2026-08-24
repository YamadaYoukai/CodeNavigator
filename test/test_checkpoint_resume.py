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
    RepositoryAliasResolver,
    RepositoryHint,
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
    call_resolver=None,
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
        call_resolver=call_resolver,
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


def write_process_a_checkpoint(
    tmp_path,
    *,
    tool_arguments=None,
    repository_hints=(),
    call_resolver=None,
    remaining_tool_calls=1,
):
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
                arguments=tool_arguments
                or {"query": "DateTime convert formats", "repo": "click"},
            ),
        ),
        store=store,
        search_handler=search_code,
        on_checkpoint_saved=interrupt_after_durable_save,
        call_resolver=call_resolver,
    )
    state = ContextState(
        system_instruction="Answer only from verified Click source evidence.",
        current_task=TASK,
        repository_hints=repository_hints,
        evidence_item_budget=0,
        remaining_tool_calls=remaining_tool_calls,
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


def _event_payload(payload, event_type: str):
    return next(
        event
        for event in payload["trace"]["events"]
        if event["event_type"] == event_type
    )


@pytest.mark.parametrize(
    ("argument_name", "tampered_value"),
    [
        pytest.param("query", "tampered query", id="query"),
        pytest.param("path", "tampered/path", id="path"),
        pytest.param("limit", 4, id="limit"),
        pytest.param("literal", False, id="literal"),
    ],
)
def test_resumable_rejects_non_repository_tool_argument_drift(
    tmp_path,
    argument_name: str,
    tampered_value: object,
) -> None:
    _, checkpoint, _ = write_process_a_checkpoint(
        tmp_path,
        tool_arguments={
            "query": "DateTime convert formats",
            "repo": "click",
            "path": "src/click",
            "limit": 3,
            "literal": True,
        },
    )
    payload = deepcopy(checkpoint.to_payload())
    _event_payload(payload, "tool_call")["arguments"][argument_name] = (
        tampered_value
    )

    with pytest.raises(CheckpointContractError):
        checkpoint_from_dict(payload)


@pytest.mark.parametrize("model_repository", ["CLICK", "unknown/click", "shared"])
def test_resumable_rejects_repository_values_that_are_not_unique_exact_aliases(
    tmp_path,
    model_repository: str,
) -> None:
    hints = (
        RepositoryHint(
            canonical_name="click",
            aliases=("pallets/click", "shared"),
        ),
        RepositoryHint(
            canonical_name="click-fork",
            aliases=("shared",),
        ),
    )
    resolver = RepositoryAliasResolver(hints)
    _, checkpoint, _ = write_process_a_checkpoint(
        tmp_path,
        repository_hints=hints,
        call_resolver=resolver,
    )
    payload = deepcopy(checkpoint.to_payload())
    model_result = _event_payload(payload, "model_result")
    model_result["decision"]["arguments"]["repo"] = model_repository

    with pytest.raises(CheckpointContractError):
        checkpoint_from_dict(payload)


@pytest.mark.parametrize("model_repository", ["click", "pallets/click"])
def test_resumable_accepts_original_or_uniquely_resolved_repository_name(
    tmp_path,
    model_repository: str,
) -> None:
    hints = (
        RepositoryHint(
            canonical_name="click",
            aliases=("pallets/click",),
        ),
    )
    resolver = RepositoryAliasResolver(hints)
    store, checkpoint, _ = write_process_a_checkpoint(
        tmp_path,
        tool_arguments={
            "query": "DateTime convert formats",
            "repo": model_repository,
        },
        repository_hints=hints,
        call_resolver=resolver,
    )

    call = next(
        event for event in checkpoint.restore_trace().events
        if isinstance(event, ToolCall)
    )
    assert call.arguments["repo"] == "click"
    assert store.load() == checkpoint

    resumed_trace = checkpoint.restore_trace()
    loop, model = build_loop(
        trace=resumed_trace,
        decisions=(
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
        search_handler=lambda **_: pytest.fail("completed Tool must not repeat"),
        call_resolver=resolver,
    )

    outcome = asyncio.run(loop.resume(checkpoint))

    assert model.model_inputs == (checkpoint.next_model_input,)
    assert outcome.final_answer.termination_reason == "completed"


def write_completed_checkpoint(tmp_path):
    store, checkpoint, _ = write_process_a_checkpoint(tmp_path)
    trace = checkpoint.restore_trace()
    loop, _ = build_loop(
        trace=trace,
        decisions=(
            FinalAnswerDecision(
                answer="Click tries every configured format before failing.",
                evidence=(
                    FinalAnswerCitation(
                        repo="click",
                        path="src/click/types.py",
                        line=491,
                    ),
                ),
                uncertainties=("Only the recorded source line was checked.",),
                next_queries=("Inspect the conversion loop.",),
            ),
        ),
        store=store,
        search_handler=lambda **_: pytest.fail("completed Tool must not repeat"),
    )
    asyncio.run(loop.resume(checkpoint))
    completed = store.load()
    assert isinstance(completed, CompletedCheckpoint)
    return completed


def test_completed_rejects_tool_argument_drift(tmp_path) -> None:
    completed = write_completed_checkpoint(tmp_path)
    payload = deepcopy(completed.to_payload())
    _event_payload(payload, "tool_call")["arguments"]["query"] = (
        "tampered completed query"
    )

    with pytest.raises(CheckpointContractError):
        checkpoint_from_dict(payload)


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        pytest.param("answer", "tampered final answer", id="answer"),
        pytest.param("evidence", [], id="evidence"),
        pytest.param("uncertainties", [], id="uncertainties"),
        pytest.param("next_queries", [], id="next-queries"),
        pytest.param("termination_reason", "tool_error", id="termination-reason"),
    ],
)
def test_completed_rejects_final_answer_drift(
    tmp_path,
    field_name: str,
    tampered_value: object,
) -> None:
    completed = write_completed_checkpoint(tmp_path)
    payload = deepcopy(completed.to_payload())
    payload["trace"]["events"][-1][field_name] = tampered_value

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


def test_resume_rejects_tampered_tool_arguments_before_model_or_tool(
    tmp_path,
) -> None:
    store, checkpoint, _ = write_process_a_checkpoint(tmp_path)
    payload = deepcopy(checkpoint.to_payload())
    _event_payload(payload, "tool_call")["arguments"]["query"] = (
        "tampered query"
    )
    tampered = checkpoint.model_copy(
        update={"trace": payload["trace"]},
        deep=True,
    )
    store.path.write_text(tampered.to_json() + "\n", encoding="utf-8")
    trace = tampered.restore_trace()
    original_trace = trace.to_dict()
    model = SpyModel()
    router = SpyRouter()
    loop = AgentLoop(
        context_builder=ContextBuilder(),
        model=model,
        tool_step_executor=ToolStepExecutor(router=router),
        trace=trace,
        checkpoint_store=store,
    )

    with pytest.raises(CheckpointResumeError, match="contract is invalid"):
        asyncio.run(loop.resume(tampered))

    assert model.calls == 0
    assert router.calls == 0
    assert trace.to_dict() == original_trace


class TerminalBoundaryModel:
    def __init__(self, trigger: object) -> None:
        self.trigger = trigger
        self.calls = 0

    def decide(self, _: ModelInput) -> object:
        self.calls += 1
        if isinstance(self.trigger, BaseException):
            raise self.trigger
        return self.trigger


def resume_with_boundary_model(
    checkpoint: ResumableCheckpoint,
    store: FileCheckpointStore,
    model: TerminalBoundaryModel,
    router: object,
):
    trace = checkpoint.restore_trace()
    traced_model = TracedModelClient(
        client=model,  # type: ignore[arg-type]
        model="terminal-boundary-model",
        trace=trace,
        request_id_factory=lambda: "terminal-request",
        clock=lambda: 0.0,
    )
    loop = AgentLoop(
        context_builder=ContextBuilder(),
        model=traced_model,
        tool_step_executor=ToolStepExecutor(router=router),  # type: ignore[arg-type]
        trace=trace,
        checkpoint_store=store,
    )
    outcome = asyncio.run(loop.resume(checkpoint))
    completed = store.load()
    assert isinstance(completed, CompletedCheckpoint)
    return outcome, completed


@pytest.mark.parametrize(
    ("trigger", "expected_reason"),
    [
        pytest.param(
            RuntimeError("private model error"),
            "model_execution_error",
            id="model-execution-error",
        ),
        pytest.param(
            {"invalid": "decision"},
            "invalid_model_output",
            id="invalid-model-output",
        ),
        pytest.param(
            TimeoutError("private timeout"),
            "model_timeout",
            id="model-timeout",
        ),
    ],
)
def test_completed_accepts_correlated_model_failure_terminals(
    tmp_path,
    trigger: object,
    expected_reason: str,
) -> None:
    store, checkpoint, _ = write_process_a_checkpoint(tmp_path)
    model = TerminalBoundaryModel(trigger)
    router = SpyRouter()

    outcome, completed = resume_with_boundary_model(
        checkpoint,
        store,
        model,
        router,
    )

    assert model.calls == 1
    assert router.calls == 0
    assert outcome.final_answer.termination_reason == expected_reason
    assert completed.trace["events"][-1]["termination_reason"] == expected_reason


def test_completed_accepts_insufficient_evidence_terminal(tmp_path) -> None:
    store, checkpoint, _ = write_process_a_checkpoint(tmp_path)
    trace = checkpoint.restore_trace()
    loop, _ = build_loop(
        trace=trace,
        decisions=(FinalAnswerDecision(answer="Unsupported answer."),),
        store=store,
        search_handler=lambda **_: pytest.fail("completed Tool must not repeat"),
    )

    outcome = asyncio.run(loop.resume(checkpoint))

    assert outcome.final_answer.termination_reason == "insufficient_evidence"
    assert isinstance(store.load(), CompletedCheckpoint)


def test_completed_accepts_tool_budget_terminal(tmp_path) -> None:
    store, checkpoint, _ = write_process_a_checkpoint(tmp_path)
    trace = checkpoint.restore_trace()
    loop, _ = build_loop(
        trace=trace,
        decisions=(
            ToolCallDecision(
                call_id="call-over-budget",
                tool_name=SEARCH_CODE,
                arguments={"query": "must not execute", "repo": "click"},
            ),
        ),
        store=store,
        search_handler=lambda **_: pytest.fail("over-budget Tool must not run"),
    )

    outcome = asyncio.run(loop.resume(checkpoint))

    assert outcome.final_answer.termination_reason == "tool_budget_exhausted"
    assert isinstance(store.load(), CompletedCheckpoint)


@pytest.mark.parametrize(
    ("exception_type", "expected_reason"),
    [
        pytest.param(RuntimeError, "tool_error", id="tool-error"),
        pytest.param(TimeoutError, "tool_timeout", id="tool-timeout"),
    ],
)
def test_completed_accepts_correlated_tool_failure_terminals(
    tmp_path,
    exception_type: type[Exception],
    expected_reason: str,
) -> None:
    store, checkpoint, _ = write_process_a_checkpoint(
        tmp_path,
        remaining_tool_calls=2,
    )
    trace = checkpoint.restore_trace()

    def fail_tool(**_: object) -> object:
        raise exception_type("private Tool failure")

    loop, _ = build_loop(
        trace=trace,
        decisions=(
            ToolCallDecision(
                call_id="call-failure",
                tool_name=SEARCH_CODE,
                arguments={"query": "trigger failure", "repo": "click"},
            ),
        ),
        store=store,
        search_handler=fail_tool,
    )

    outcome = asyncio.run(loop.resume(checkpoint))

    assert outcome.final_answer.termination_reason == expected_reason
    assert isinstance(store.load(), CompletedCheckpoint)


class MissingResultRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, _: ToolCall) -> None:
        self.calls += 1


def test_completed_accepts_explainable_harness_invariant_terminal(
    tmp_path,
) -> None:
    store, checkpoint, _ = write_process_a_checkpoint(
        tmp_path,
        remaining_tool_calls=2,
    )
    model = TerminalBoundaryModel(
        ToolCallDecision(
            call_id="call-missing-result",
            tool_name=SEARCH_CODE,
            arguments={"query": "missing result", "repo": "click"},
        )
    )
    router = MissingResultRouter()

    outcome, completed = resume_with_boundary_model(
        checkpoint,
        store,
        model,
        router,
    )

    assert model.calls == 1
    assert router.calls == 1
    assert outcome.final_answer.termination_reason == "harness_invariant_error"
    assert completed.tool_calls_used == 2


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
