#!/usr/bin/env python3
"""Replay one persisted, provider-independent tool decision exactly once."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from src.examples.code_understanding_agent.context import (
    ContextState,
    Evidence,
    EvidenceKind,
)
from src.examples.code_understanding_agent.events import ToolCall, ToolResult
from src.examples.code_understanding_agent.model_boundary import ToolCallDecision
from src.examples.code_understanding_agent.repository_resolver import (
    RepositoryAliasResolver,
)
from src.examples.code_understanding_agent.tool_router import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    GetFileContextArguments,
    PydanticToolAdapter,
    SearchCodeArguments,
    ToolRouter,
)
from src.examples.code_understanding_agent.tool_step import (
    ToolStepExecutor,
    ToolStepOutcome,
)
from src.examples.code_understanding_agent.trace import TraceRecorder


EXPECTED_TOP_MATCH = {
    "repo": "click",
    "path": "src/click/core.py",
    "line": 1221,
    "snippet": "def make_context(",
}

ToolHandler = Callable[..., object]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _PersistedRepositoryHint(_StrictModel):
    canonical_name: str
    aliases: list[str]


class _PersistedEvidence(_StrictModel):
    kind: Literal["fact", "history"]
    source: str
    content: str


class _PersistedContextState(_StrictModel):
    system_instruction: str
    current_task: str
    repository_hints: list[_PersistedRepositoryHint]
    evidence: list[_PersistedEvidence]
    evidence_item_budget: int
    remaining_tool_calls: int


class _PersistedSearchArguments(_StrictModel):
    query: str
    repo: str | None
    lang: str | None
    path: str | None
    limit: int
    literal: bool


class _PersistedToolCallDecision(_StrictModel):
    decision_type: Literal["tool_call"]
    call_id: str
    tool_name: Literal["search_code"]
    arguments: _PersistedSearchArguments


class ReplayFixture(_StrictModel):
    """Complete public fields required to replay this persisted decision."""

    schema_version: Literal[1]
    context_state: _PersistedContextState
    decision: _PersistedToolCallDecision


class BudgetReport(_StrictModel):
    before: int
    after: int


class ToolResultReport(_StrictModel):
    status: Literal["success"]


class TopMatchReport(_StrictModel):
    repo: str
    path: str
    line: int
    snippet: str


class FactEvidenceReport(_StrictModel):
    kind: Literal["fact"]
    source: str
    content: str


class NextModelInputReport(_StrictModel):
    remaining_tool_calls: int
    new_fact_evidence: FactEvidenceReport


class ReplayReport(_StrictModel):
    """Allowlisted, public output from one successful replay."""

    model_repo: str
    executed_repo: str
    trace_suffix: tuple[Literal["tool_call"], Literal["tool_result"]]
    budget: BudgetReport
    tool_result: ToolResultReport
    top_match: TopMatchReport
    next_model_input: NextModelInputReport


@dataclass(frozen=True, slots=True)
class ValidatedReplayFixture:
    context_state: ContextState
    decision: ToolCallDecision


@dataclass(frozen=True, slots=True)
class ReplayExecution:
    fixture: ValidatedReplayFixture
    trace: TraceRecorder
    outcome: ToolStepOutcome
    report: ReplayReport


class ReplayError(RuntimeError):
    """A stable public failure category with no backend exception detail."""

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__(error_type)


def load_fixture(path: Path) -> ValidatedReplayFixture:
    """Read the complete public fixture and validate both domain contracts."""

    decoded = json.loads(path.read_text(encoding="utf-8"))
    persisted = ReplayFixture.model_validate(decoded)
    state = ContextState.model_validate(
        persisted.context_state.model_dump(mode="python")
    )
    decision = ToolCallDecision.model_validate(
        persisted.decision.model_dump(mode="python")
    )
    return ValidatedReplayFixture(context_state=state, decision=decision)


async def execute_replay(
    fixture: ValidatedReplayFixture,
    *,
    search_code: ToolHandler,
    get_file_context: ToolHandler,
) -> ReplayExecution:
    """Execute the fixture's validated decision once with injected tools."""

    state = fixture.context_state
    decision = fixture.decision
    decision_before = decision.model_dump(mode="json")
    trace = TraceRecorder(task_id="public-click-tool-step-replay")
    resolver = RepositoryAliasResolver(state.repository_hints)
    router = ToolRouter(
        trace=trace,
        tools={
            SEARCH_CODE: PydanticToolAdapter(SearchCodeArguments, search_code),
            GET_FILE_CONTEXT: PydanticToolAdapter(
                GetFileContextArguments,
                get_file_context,
            ),
        },
        call_resolver=resolver,
    )
    outcome = await ToolStepExecutor(router=router).execute(state, decision)

    if decision.model_dump(mode="json") != decision_before:
        raise ReplayError("decision_mutated")
    report = _build_report(state, decision, trace, outcome)
    return ReplayExecution(
        fixture=fixture,
        trace=trace,
        outcome=outcome,
        report=report,
    )


async def replay_fixture(
    path: Path,
    *,
    search_code: ToolHandler,
    get_file_context: ToolHandler,
) -> ReplayExecution:
    """Load and execute one public replay fixture."""

    return await execute_replay(
        load_fixture(path),
        search_code=search_code,
        get_file_context=get_file_context,
    )


def _build_report(
    state: ContextState,
    decision: ToolCallDecision,
    trace: TraceRecorder,
    outcome: ToolStepOutcome,
) -> ReplayReport:
    model_repo = decision.arguments.get("repo")
    events = trace.events
    if not isinstance(model_repo, str):
        raise ReplayError("unexpected_result")
    if len(events) != 2 or not isinstance(events[0], ToolCall):
        raise ReplayError("unexpected_trace")
    if not isinstance(events[1], ToolResult):
        raise ReplayError("unexpected_trace")
    if [event.event_type for event in events] != ["tool_call", "tool_result"]:
        raise ReplayError("unexpected_trace")

    recorded_call = events[0]
    recorded_result = events[1]
    executed_repo = recorded_call.arguments.get("repo")
    if not isinstance(executed_repo, str):
        raise ReplayError("unexpected_result")
    if outcome.tool_result is None or recorded_result != outcome.tool_result:
        raise ReplayError("unexpected_trace")
    if outcome.tool_result.status != "success":
        raise ReplayError("tool_failed")
    if state.remaining_tool_calls != 1:
        raise ReplayError("unexpected_budget")
    if (
        outcome.next_state.remaining_tool_calls != 0
        or outcome.next_model_input.remaining_tool_calls != 0
    ):
        raise ReplayError("unexpected_budget")

    result = outcome.tool_result.result
    if not isinstance(result, dict):
        raise ReplayError("unexpected_result")
    matches = result.get("matches")
    if not isinstance(matches, list) or not matches:
        raise ReplayError("unexpected_result")
    try:
        top_match = TopMatchReport.model_validate(matches[0])
    except ValidationError:
        raise ReplayError("unexpected_result") from None
    if top_match.model_dump(mode="json") != EXPECTED_TOP_MATCH:
        raise ReplayError("unexpected_result")
    if model_repo != "pallets/click" or executed_repo != "click":
        raise ReplayError("unexpected_result")

    evidence_source = f"tool_result:{decision.tool_name}:{decision.call_id}"
    fresh_evidence = tuple(
        item
        for item in outcome.next_model_input.evidence
        if item.kind is EvidenceKind.FACT and item.source == evidence_source
    )
    if len(fresh_evidence) != 1:
        raise ReplayError("unexpected_model_input")
    evidence = fresh_evidence[0]
    if not isinstance(evidence, Evidence):
        raise ReplayError("unexpected_model_input")

    return ReplayReport(
        model_repo=model_repo,
        executed_repo=executed_repo,
        trace_suffix=("tool_call", "tool_result"),
        budget=BudgetReport(before=1, after=0),
        tool_result=ToolResultReport(status="success"),
        top_match=top_match,
        next_model_input=NextModelInputReport(
            remaining_tool_calls=outcome.next_model_input.remaining_tool_calls,
            new_fact_evidence=FactEvidenceReport(
                kind="fact",
                source=evidence.source,
                content=evidence.content,
            ),
        ),
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay one persisted model tool decision without a model call."
    )
    parser.add_argument("--fixture", required=True, type=Path)
    return parser.parse_args(argv)


def _print_error(error_type: str) -> None:
    print(
        json.dumps(
            {"status": "error", "error_type": error_type},
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        fixture = load_fixture(args.fixture)
    except (OSError, json.JSONDecodeError, ValidationError):
        _print_error("invalid_fixture")
        return 2

    try:
        from src.server import get_file_context, search_code

        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        execution = asyncio.run(
            execute_replay(
                fixture,
                search_code=search_code,
                get_file_context=get_file_context,
            )
        )
    except ReplayError as exc:
        _print_error(exc.error_type)
        return 3
    except Exception:
        _print_error("replay_failed")
        return 4

    print(
        json.dumps(
            execution.report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
