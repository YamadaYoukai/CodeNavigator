import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock

from src.examples.code_understanding_agent import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    ContextState,
    EvidenceKind,
    GetFileContextArguments,
    PydanticToolAdapter,
    RepositoryAliasResolver,
    RepositoryHint,
    SearchCodeArguments,
    ToolCallDecision,
    ToolErrorCode,
    ToolRouter,
    ToolStepExecutor,
    TraceRecorder,
)


def build_state(
    *,
    remaining_tool_calls: int,
    evidence_item_budget: int = 0,
    repository_hints: tuple[RepositoryHint, ...] | None = None,
) -> ContextState:
    if repository_hints is None:
        repository_hints = (
            RepositoryHint(
                canonical_name="retry-service",
                aliases=("example/retry-service",),
            ),
        )

    return ContextState(
        system_instruction="Answer only from supplied code evidence.",
        current_task="Locate the retry policy.",
        repository_hints=repository_hints,
        evidence_item_budget=evidence_item_budget,
        remaining_tool_calls=remaining_tool_calls,
    )


def build_router(
    trace: TraceRecorder,
    search_handler: Callable[..., object],
    *,
    resolver: RepositoryAliasResolver | None = None,
) -> ToolRouter:
    def get_file_context(**_: object) -> dict[str, object]:
        return {"content": "unused"}

    return ToolRouter(
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
        call_resolver=resolver,
    )


def test_should_execute_one_successful_transition_and_include_fresh_result() -> None:
    trace = TraceRecorder(task_id="tool-step-success")
    received_arguments: list[dict[str, object]] = []

    def search_code(**arguments: object) -> dict[str, object]:
        received_arguments.append(arguments)
        return {
            "matches": [{"line": 12, "path": "src/retry.py"}],
            "query": "retry policy",
        }

    router = build_router(trace, search_code)
    executor = ToolStepExecutor(router=router)
    state = build_state(remaining_tool_calls=2, evidence_item_budget=0)
    decision = ToolCallDecision(
        call_id="call-success",
        tool_name=SEARCH_CODE,
        arguments={"query": "retry policy", "limit": 3},
    )
    original_state = state.model_dump(mode="json")
    original_decision = decision.model_dump(mode="json")

    outcome = asyncio.run(executor.execute(state, decision))

    assert outcome.tool_result is not None
    assert outcome.tool_result.status == "success"
    assert outcome.next_state.remaining_tool_calls == 1
    assert outcome.next_model_input.remaining_tool_calls == 1
    assert [event.event_type for event in trace.events] == [
        "tool_call",
        "tool_result",
    ]
    assert received_arguments == [
        {
            "query": "retry policy",
            "repo": None,
            "lang": None,
            "path": None,
            "limit": 3,
            "literal": False,
        }
    ]
    assert len(outcome.next_model_input.evidence) == 1
    fresh_evidence = outcome.next_model_input.evidence[0]
    assert fresh_evidence.kind is EvidenceKind.FACT
    assert fresh_evidence.source == "tool_result:search_code:call-success"
    assert fresh_evidence.content == (
        '{"result":{"matches":[{"line":12,"path":"src/retry.py"}],'
        '"query":"retry policy"},"status":"success"}'
    )
    assert outcome.next_state.evidence[0] == fresh_evidence
    assert state.model_dump(mode="json") == original_state
    assert decision.model_dump(mode="json") == original_decision


def test_should_resolve_alias_through_full_tool_step_without_mutating_decision() -> None:
    trace = TraceRecorder(task_id="tool-step-repository-alias")
    received_arguments: list[dict[str, object]] = []

    def search_code(**arguments: object) -> dict[str, object]:
        received_arguments.append(arguments)
        return {
            "matches": [{"line": 1169, "path": "src/click/core.py"}],
            "query": "def make_context",
        }

    repository_hint = RepositoryHint(
        canonical_name="click",
        aliases=("pallets/click",),
    )
    resolver = RepositoryAliasResolver((repository_hint,))
    executor = ToolStepExecutor(
        router=build_router(trace, search_code, resolver=resolver)
    )
    state = build_state(
        remaining_tool_calls=2,
        evidence_item_budget=0,
        repository_hints=(repository_hint,),
    )
    decision = ToolCallDecision(
        call_id="call-repository-alias",
        tool_name=SEARCH_CODE,
        arguments={
            "query": "def make_context",
            "repo": "pallets/click",
        },
    )
    original_decision = decision.model_dump(mode="json")

    outcome = asyncio.run(executor.execute(state, decision))

    assert received_arguments == [
        {
            "query": "def make_context",
            "repo": "click",
            "lang": None,
            "path": None,
            "limit": 20,
            "literal": False,
        }
    ]
    recorded_call, recorded_result = trace.events
    assert [event.event_type for event in trace.events] == [
        "tool_call",
        "tool_result",
    ]
    assert recorded_call.arguments["repo"] == "click"
    assert recorded_result == outcome.tool_result
    assert decision.model_dump(mode="json") == original_decision
    assert decision.arguments["repo"] == "pallets/click"
    assert outcome.next_state.remaining_tool_calls == 1
    assert outcome.next_model_input.remaining_tool_calls == 1
    assert outcome.next_model_input.evidence[0].content == (
        '{"result":{"matches":[{"line":1169,"path":"src/click/core.py"}],'
        '"query":"def make_context"},"status":"success"}'
    )


def test_should_classify_tool_exception_and_consume_budget_only_once() -> None:
    trace = TraceRecorder(task_id="tool-step-exception")
    invocations = 0

    def search_code(**_: object) -> object:
        nonlocal invocations
        invocations += 1
        raise RuntimeError("private backend exception text")

    executor = ToolStepExecutor(router=build_router(trace, search_code))
    state = build_state(remaining_tool_calls=2)
    decision = ToolCallDecision(
        call_id="call-exception",
        tool_name=SEARCH_CODE,
        arguments={"query": "retry policy"},
    )

    outcome = asyncio.run(executor.execute(state, decision))

    assert outcome.tool_result is not None
    assert outcome.tool_result.status == "error"
    assert outcome.tool_result.error_type == ToolErrorCode.TOOL_EXECUTION_ERROR.value
    assert outcome.next_state.remaining_tool_calls == 1
    assert invocations == 1
    assert outcome.next_model_input.evidence[0].kind is EvidenceKind.FACT
    assert outcome.next_model_input.evidence[0].content == (
        '{"error_type":"tool_execution_error","status":"error"}'
    )
    assert "private backend exception text" not in trace.to_json()
    assert "private backend exception text" not in outcome.next_model_input.to_json()


def test_should_classify_unknown_repository_without_invoking_backing_tool() -> None:
    trace = TraceRecorder(task_id="tool-step-unknown-repository")
    invoked = False

    def search_code(**_: object) -> dict[str, object]:
        nonlocal invoked
        invoked = True
        return {"matches": []}

    resolver = RepositoryAliasResolver(
        (RepositoryHint(canonical_name="retry-service"),)
    )
    executor = ToolStepExecutor(
        router=build_router(trace, search_code, resolver=resolver)
    )
    state = build_state(remaining_tool_calls=2)
    decision = ToolCallDecision(
        call_id="call-unknown-repository",
        tool_name=SEARCH_CODE,
        arguments={"query": "retry policy", "repo": "unknown/repository"},
    )

    outcome = asyncio.run(executor.execute(state, decision))

    assert outcome.tool_result is not None
    assert outcome.tool_result.status == "error"
    assert outcome.tool_result.error_type == ToolErrorCode.UNKNOWN_REPOSITORY.value
    assert outcome.next_state.remaining_tool_calls == 1
    assert invoked is False
    assert [event.event_type for event in trace.events] == [
        "tool_call",
        "tool_result",
    ]
    assert outcome.next_model_input.evidence[0].content == (
        '{"error_type":"unknown_repository","status":"error"}'
    )


def test_should_leave_state_and_trace_untouched_when_tool_budget_is_exhausted() -> None:
    trace = TraceRecorder(task_id="tool-step-exhausted")
    adapter_invoked = False

    def search_code(**_: object) -> dict[str, object]:
        nonlocal adapter_invoked
        adapter_invoked = True
        return {"matches": []}

    router = build_router(trace, search_code)
    router_execute = AsyncMock(wraps=router.execute)
    router.execute = router_execute  # type: ignore[method-assign]
    executor = ToolStepExecutor(router=router)
    state = build_state(remaining_tool_calls=0)
    decision = ToolCallDecision(
        call_id="call-exhausted",
        tool_name=SEARCH_CODE,
        arguments={"query": "retry policy"},
    )
    original_decision = decision.model_dump(mode="json")

    outcome = asyncio.run(executor.execute(state, decision))

    assert outcome.tool_result is None
    assert outcome.next_state is state
    assert outcome.next_model_input.remaining_tool_calls == 0
    assert trace.events == ()
    router_execute.assert_not_awaited()
    assert adapter_invoked is False
    assert decision.model_dump(mode="json") == original_decision
