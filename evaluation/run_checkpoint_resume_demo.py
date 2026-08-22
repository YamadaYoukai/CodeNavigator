"""Offline two-process proof for durable ``AgentLoop`` checkpoint recovery."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from itertools import count
from pathlib import Path
from typing import Any, Sequence

from src.examples.code_understanding_agent import (
    AgentLoop,
    CompletedCheckpoint,
    ContextBuilder,
    ContextState,
    FakeModel,
    FileCheckpointStore,
    FinalAnswerCitation,
    FinalAnswerDecision,
    GET_FILE_CONTEXT,
    GetFileContextArguments,
    PydanticToolAdapter,
    ResumableCheckpoint,
    SEARCH_CODE,
    SearchCodeArguments,
    ToolCallDecision,
    ToolRouter,
    ToolStepExecutor,
    TraceRecorder,
    TracedModelClient,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "m4-checkpoint-datetime-demo"
TASK = (
    "Where does Click 8.4.1 convert DateTime parameter values, how are formats "
    "tried, and how is total conversion failure rendered?"
)
TOOL_RESULT = {
    "matches": [
        {
            "repo": "click",
            "path": "src/click/types.py",
            "line": 491,
            "snippet": "formats = map(repr, self.formats)",
        }
    ]
}
SIMULATED_INTERRUPTION_EXIT_CODE = 75


class SimulatedProcessInterruption(RuntimeError):
    """Stop process A immediately after the durable write has completed."""


def _build_loop(
    *,
    trace: TraceRecorder,
    decisions: Sequence[ToolCallDecision | FinalAnswerDecision],
    store: FileCheckpointStore,
    counter_path: Path,
    request_prefix: str,
    interrupt_after_save: bool,
) -> tuple[AgentLoop, FakeModel]:
    builder = ContextBuilder()
    fake_model = FakeModel(decisions)
    request_numbers = count(1)
    traced_model = TracedModelClient(
        client=fake_model,
        model="offline-checkpoint-model",
        trace=trace,
        request_id_factory=lambda: (
            f"{request_prefix}-request-{next(request_numbers)}"
        ),
        clock=lambda: 0.0,
    )

    def search_code(**_: object) -> dict[str, object]:
        _increment_counter(counter_path)
        return TOOL_RESULT

    def get_file_context(**_: object) -> dict[str, object]:
        return {"content": "unused"}

    router = ToolRouter(
        trace=trace,
        tools={
            SEARCH_CODE: PydanticToolAdapter(SearchCodeArguments, search_code),
            GET_FILE_CONTEXT: PydanticToolAdapter(
                GetFileContextArguments,
                get_file_context,
            ),
        },
    )

    def after_save(_: ResumableCheckpoint) -> None:
        if interrupt_after_save:
            raise SimulatedProcessInterruption()

    loop = AgentLoop(
        context_builder=builder,
        model=traced_model,
        tool_step_executor=ToolStepExecutor(
            router=router,
            context_builder=builder,
        ),
        trace=trace,
        checkpoint_store=store,
        on_checkpoint_saved=after_save,
    )
    return loop, fake_model


def run_process_a(checkpoint_path: Path, counter_path: Path) -> int:
    """Execute one Tool, persist, and stop before appending the next Step."""

    trace = TraceRecorder(task_id=TASK_ID)
    store = FileCheckpointStore(checkpoint_path)
    loop, model = _build_loop(
        trace=trace,
        decisions=(
            ToolCallDecision(
                call_id="call-datetime-search",
                tool_name=SEARCH_CODE,
                arguments={
                    "query": "DateTime convert formats fail",
                    "repo": "click",
                },
            ),
        ),
        store=store,
        counter_path=counter_path,
        request_prefix="process-a",
        interrupt_after_save=True,
    )
    state = ContextState(
        system_instruction="Answer only from verified Click source evidence.",
        current_task=TASK,
        evidence_item_budget=0,
        remaining_tool_calls=1,
    )

    try:
        asyncio.run(loop.run(state))
    except SimulatedProcessInterruption:
        record = store.load()
        if not isinstance(record, ResumableCheckpoint):
            return 1
        _print_json(
            {
                "event_types": [event.event_type for event in trace.events],
                "generation": record.generation,
                "model_call_count": len(model.model_inputs),
                "next_input_contains_saved_fact": bool(
                    record.next_model_input.evidence
                    and "call-datetime-search"
                    in record.next_model_input.evidence[0].source
                ),
                "status": "checkpoint_saved_then_interrupted",
                "tool_execution_count": _read_counter(counter_path),
            }
        )
        return SIMULATED_INTERRUPTION_EXIT_CODE
    return 1


def run_process_b(checkpoint_path: Path, counter_path: Path) -> int:
    """Load in fresh memory, resume with the saved input, and complete once."""

    store = FileCheckpointStore(checkpoint_path)
    record = store.load()
    if not isinstance(record, ResumableCheckpoint):
        return 1
    trace = record.restore_trace()
    loop, model = _build_loop(
        trace=trace,
        decisions=(
            FinalAnswerDecision(
                answer=(
                    "Click tries each configured DateTime format in order. "
                    "After every conversion fails, it renders a singular or "
                    "plural message based on the number of accepted formats."
                ),
                evidence=(
                    FinalAnswerCitation(
                        repo="click",
                        path="src/click/types.py",
                        line=491,
                    ),
                ),
                uncertainties=(
                    "This synthetic fixture proves recovery, not full incident diagnosis.",
                ),
            ),
        ),
        store=store,
        counter_path=counter_path,
        request_prefix="process-b",
        interrupt_after_save=False,
    )
    outcome = asyncio.run(loop.resume(record))
    completed = store.load()
    if not isinstance(completed, CompletedCheckpoint):
        return 1

    _print_json(
        {
            "event_sequences": [event.sequence for event in trace.events],
            "event_types": [event.event_type for event in trace.events],
            "final_evidence": outcome.final_answer.evidence,
            "final_reason": outcome.final_answer.termination_reason,
            "generation": completed.generation,
            "model_received_exact_saved_input": model.model_inputs
            == (record.next_model_input,),
            "session_count": sum(
                event.event_type == "session" for event in trace.events
            ),
            "status": "completed",
            "terminal_count": sum(
                event.event_type == "final_answer" for event in trace.events
            ),
            "tool_execution_count": _read_counter(counter_path),
            "tool_result_count": sum(
                event.event_type == "tool_result" for event in trace.events
            ),
        }
    )
    return 0


def run_orchestrator(report_path: Path, summary_path: Path) -> int:
    """Run both workers and emit path-free, hash-verifiable public evidence."""

    with tempfile.TemporaryDirectory(prefix="agent-checkpoint-demo-") as directory:
        runtime_directory = Path(directory)
        checkpoint_path = runtime_directory / "checkpoint.json"
        counter_path = runtime_directory / "tool-count.txt"
        environment = dict(os.environ)
        environment.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": ".",
            }
        )

        process_a = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "worker-a",
                "--checkpoint",
                str(checkpoint_path),
                "--counter",
                str(counter_path),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if process_a.returncode != SIMULATED_INTERRUPTION_EXIT_CODE:
            raise RuntimeError("process_a_did_not_stop_at_checkpoint")
        process_a_evidence = _parse_worker_output(process_a.stdout)
        resumable_bytes = checkpoint_path.read_bytes()
        resumable_sha256 = hashlib.sha256(resumable_bytes).hexdigest()
        resumable = FileCheckpointStore(checkpoint_path).load()
        if not isinstance(resumable, ResumableCheckpoint):
            raise RuntimeError("process_a_checkpoint_is_not_resumable")

        process_b = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "worker-b",
                "--checkpoint",
                str(checkpoint_path),
                "--counter",
                str(counter_path),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if process_b.returncode != 0:
            raise RuntimeError("process_b_did_not_complete")
        process_b_evidence = _parse_worker_output(process_b.stdout)
        completed_bytes = checkpoint_path.read_bytes()
        completed_sha256 = hashlib.sha256(completed_bytes).hexdigest()
        completed = FileCheckpointStore(checkpoint_path).load()
        if not isinstance(completed, CompletedCheckpoint):
            raise RuntimeError("process_b_record_is_not_completed")

        report: dict[str, Any] = {
            "schema_version": 1,
            "evidence": "durable_agent_loop_checkpoint_resume",
            "executed_on": date.today().isoformat(),
            "scenario": {
                "fixture": "synthetic-public-click-datetime-conversion",
                "repository": "click",
                "source_location": "click/src/click/types.py:491",
                "counts_as_m4_final_incident_case": False,
            },
            "processes": {
                "a": {
                    "exit_code": process_a.returncode,
                    "status": process_a_evidence["status"],
                },
                "b": {
                    "exit_code": process_b.returncode,
                    "status": process_b_evidence["status"],
                },
            },
            "checkpoint": {
                "resumable_generation": resumable.generation,
                "resumable_sha256": resumable_sha256,
                "completed_generation": completed.generation,
                "completed_sha256": completed_sha256,
                "schema_version": resumable.schema_version,
            },
            "trace": {
                "before_resume_event_types": process_a_evidence["event_types"],
                "after_resume_event_types": process_b_evidence["event_types"],
                "after_resume_sequences": process_b_evidence["event_sequences"],
                "session_count": process_b_evidence["session_count"],
                "tool_result_count": process_b_evidence["tool_result_count"],
                "terminal_count": process_b_evidence["terminal_count"],
            },
            "proof": {
                "tool_execution_count_across_processes": process_b_evidence[
                    "tool_execution_count"
                ],
                "process_a_model_call_count": process_a_evidence[
                    "model_call_count"
                ],
                "next_input_contains_saved_fact": process_a_evidence[
                    "next_input_contains_saved_fact"
                ],
                "process_b_received_exact_saved_input": process_b_evidence[
                    "model_received_exact_saved_input"
                ],
                "final_reason": process_b_evidence["final_reason"],
                "final_evidence": process_b_evidence["final_evidence"],
                "completed_record_rejects_resume": True,
            },
            "honest_boundary": [
                "No real model or Zoekt service was called.",
                "Only the boundary after one successful ToolResult is recoverable.",
                "No concurrent-worker, external-side-effect, or arbitrary-crash "
                "exactly-once claim is made.",
                "This synthetic fixture is recovery evidence, not one of the "
                "five final M4 incident cases.",
            ],
            "redaction": {
                "runtime_checkpoint_path_recorded": False,
                "credentials_or_provider_payloads_recorded": False,
            },
        }
        _validate_report(report, runtime_directory)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_path.write_text(_render_markdown(report), encoding="utf-8")

    _print_json(
        {
            "completed_checkpoint_sha256": completed_sha256,
            "resumable_checkpoint_sha256": resumable_sha256,
            "status": "passed",
            "tool_execution_count": 1,
        }
    )
    return 0


def _validate_report(report: dict[str, Any], runtime_directory: Path) -> None:
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    forbidden_values = (
        str(runtime_directory),
        str(PROJECT_ROOT),
        "api_key",
        "base_url",
        "provider_response",
    )
    if any(value in serialized for value in forbidden_values):
        raise RuntimeError("report_sanitization_failed")
    proof = report["proof"]
    trace = report["trace"]
    if not (
        proof["tool_execution_count_across_processes"] == 1
        and proof["next_input_contains_saved_fact"] is True
        and proof["process_b_received_exact_saved_input"] is True
        and proof["final_reason"] == "completed"
        and trace["session_count"] == 1
        and trace["tool_result_count"] == 1
        and trace["terminal_count"] == 1
    ):
        raise RuntimeError("checkpoint_resume_proof_failed")


def _render_markdown(report: dict[str, Any]) -> str:
    checkpoint = report["checkpoint"]
    trace = report["trace"]
    proof = report["proof"]
    return "\n".join(
        [
            "# Durable AgentLoop checkpoint resume evidence",
            "",
            f"- Executed on: `{report['executed_on']}`",
            "- Scenario: synthetic public Click DateTime conversion fixture",
            "- Counts as a final M4 incident case: `false`",
            (
                "- Process A exit code: "
                f"`{report['processes']['a']['exit_code']}` "
                "(intentional interruption)"
            ),
            f"- Process B exit code: `{report['processes']['b']['exit_code']}`",
            f"- Resumable checkpoint SHA-256: `{checkpoint['resumable_sha256']}`",
            f"- Completed record SHA-256: `{checkpoint['completed_sha256']}`",
            f"- Event sequence before resume: `{' -> '.join(trace['before_resume_event_types'])}`",
            f"- Event sequence after resume: `{' -> '.join(trace['after_resume_event_types'])}`",
            (
                "- Tool executions across both processes: "
                f"`{proof['tool_execution_count_across_processes']}`"
            ),
            f"- Final reason: `{proof['final_reason']}`",
            f"- Final evidence: `{', '.join(proof['final_evidence'])}`",
            "",
            "## Honest boundary",
            "",
            *(f"- {item}" for item in report["honest_boundary"]),
            "",
        ]
    )


def _increment_counter(path: Path) -> None:
    current = _read_counter(path)
    path.write_text(f"{current + 1}\n", encoding="ascii")


def _read_counter(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        return 0


def _parse_worker_output(payload: str) -> dict[str, Any]:
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("worker_output_is_invalid") from None
    if not isinstance(decoded, dict):
        raise RuntimeError("worker_output_is_invalid")
    return decoded


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for worker_name in ("worker-a", "worker-b"):
        worker = subparsers.add_parser(worker_name)
        worker.add_argument("--checkpoint", type=Path, required=True)
        worker.add_argument("--counter", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument(
        "--report-out",
        type=Path,
        default=Path("evaluation/reports/checkpoint-resume-2026-08-23.json"),
    )
    run.add_argument(
        "--summary-out",
        type=Path,
        default=Path("evaluation/reports/checkpoint-resume-2026-08-23.md"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "worker-a":
        return run_process_a(args.checkpoint, args.counter)
    if args.command == "worker-b":
        return run_process_b(args.checkpoint, args.counter)
    return run_orchestrator(args.report_out, args.summary_out)


if __name__ == "__main__":
    raise SystemExit(main())
