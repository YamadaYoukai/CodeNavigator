import asyncio
from collections.abc import Callable

from pydantic import BaseModel

from src.examples.code_understanding_agent import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    GetFileContextArguments,
    ModelResult,
    PydanticToolAdapter,
    RepositoryAliasResolver,
    RepositoryHint,
    SearchCodeArguments,
    ToolCall,
    ToolErrorCode,
    ToolResult,
    ToolRouter,
    ToolCallResolver,
    TraceRecorder,
)


class SearchPayload(BaseModel):
    query: str
    matches: list[dict[str, str | int]]


def build_router(
    trace: TraceRecorder,
    search_handler: Callable[..., object],
    call_resolver: ToolCallResolver | None = None,
) -> ToolRouter:
    async def get_file_context(**_: object) -> dict[str, object]:
        return {"content": "unused"}

    return ToolRouter(
        trace=trace,
        tools={
            SEARCH_CODE: PydanticToolAdapter(SearchCodeArguments, search_handler),
            GET_FILE_CONTEXT: PydanticToolAdapter(
                GetFileContextArguments,
                get_file_context,
            ),
        },
        call_resolver=call_resolver,
    )


def assert_recorded_pair(
    trace: TraceRecorder,
    result: ToolResult,
    expected_call_id: str,
) -> None:
    recorded_call, recorded_result = trace.events

    assert [event.event_type for event in trace.events] == ["tool_call", "tool_result"]
    assert [event.sequence for event in trace.events] == [1, 2]
    assert recorded_call.call_id == recorded_result.call_id == expected_call_id
    assert recorded_result == result


def test_should_return_normalized_result_and_record_correlated_trace_when_tool_succeeds() -> None:
    trace = TraceRecorder(task_id="router-success")
    received_arguments: list[dict[str, object]] = []

    async def search_code(**arguments: object) -> SearchPayload:
        received_arguments.append(arguments)
        return SearchPayload(
            query="retry policy",
            matches=[{"path": "src/retry.py", "line": 12}],
        )

    router = build_router(trace, search_code)
    call = ToolCall(
        call_id="call-success",
        tool_name=SEARCH_CODE,
        arguments={"query": "retry policy", "limit": 3},
    )

    result = asyncio.run(router.execute(call))

    assert result.status == "success"
    assert result.result == {
        "query": "retry policy",
        "matches": [{"path": "src/retry.py", "line": 12}],
    }
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
    assert_recorded_pair(trace, result, "call-success")


def test_should_return_unknown_tool_and_record_correlated_trace_when_tool_is_not_allowed() -> None:
    trace = TraceRecorder(task_id="router-unknown")
    router = build_router(trace, lambda **_: {"matches": []})
    call = ToolCall(
        call_id="call-unknown",
        tool_name="list_repositories",
        arguments={},
    )

    result = asyncio.run(router.execute(call))

    assert result.status == "error"
    assert result.error_type == ToolErrorCode.UNKNOWN_TOOL.value
    assert_recorded_pair(trace, result, "call-unknown")


def test_should_return_invalid_arguments_and_skip_tool_when_arguments_do_not_validate() -> None:
    trace = TraceRecorder(task_id="router-invalid")
    invoked = False

    def search_code(**_: object) -> dict[str, object]:
        nonlocal invoked
        invoked = True
        return {"matches": []}

    router = build_router(trace, search_code)
    call = ToolCall(
        call_id="call-invalid",
        tool_name=SEARCH_CODE,
        arguments={"query": ""},
    )

    result = asyncio.run(router.execute(call))

    assert result.status == "error"
    assert result.error_type == ToolErrorCode.INVALID_ARGUMENTS.value
    assert invoked is False
    assert_recorded_pair(trace, result, "call-invalid")


def test_should_return_execution_error_and_record_correlated_trace_when_tool_raises() -> None:
    trace = TraceRecorder(task_id="router-exception")

    async def failing_search_code(**_: object) -> object:
        raise RuntimeError("zoekt is unavailable")

    router = build_router(trace, failing_search_code)
    call = ToolCall(
        call_id="call-exception",
        tool_name=SEARCH_CODE,
        arguments={"query": "retry policy"},
    )

    result = asyncio.run(router.execute(call))

    assert result.status == "error"
    assert result.error_type == ToolErrorCode.TOOL_EXECUTION_ERROR.value
    assert_recorded_pair(trace, result, "call-exception")


def test_should_trace_original_model_alias_and_canonical_executable_call() -> None:
    trace = TraceRecorder(task_id="router-repository-alias")
    trace.append(
        ModelResult(
            request_id="request-repository-alias",
            status="success",
            elapsed_ms=10,
            decision={
                "decision_type": "tool_call",
                "call_id": "call-repository-alias",
                "tool_name": SEARCH_CODE,
                "arguments": {
                    "query": "def make_context",
                    "repo": "pallets/click",
                },
            },
        )
    )
    received_arguments: list[dict[str, object]] = []

    def search_code(**arguments: object) -> dict[str, object]:
        received_arguments.append(arguments)
        return {"query": "def make_context", "matches": []}

    resolver = RepositoryAliasResolver(
        (
            RepositoryHint(
                canonical_name="click",
                aliases=("pallets/click",),
            ),
        )
    )
    router = build_router(trace, search_code, resolver)
    model_call = ToolCall(
        call_id="call-repository-alias",
        tool_name=SEARCH_CODE,
        arguments={"query": "def make_context", "repo": "pallets/click"},
    )

    result = asyncio.run(router.execute(model_call))

    model_result, executable_call, tool_result = trace.events
    assert model_result.decision["arguments"]["repo"] == "pallets/click"
    assert executable_call.arguments["repo"] == "click"
    assert tool_result == result
    assert received_arguments[0]["repo"] == "click"


def test_should_reject_unknown_repository_before_tool_execution() -> None:
    trace = TraceRecorder(task_id="router-unknown-repository")
    invoked = False

    def search_code(**_: object) -> dict[str, object]:
        nonlocal invoked
        invoked = True
        return {"matches": []}

    resolver = RepositoryAliasResolver(
        (RepositoryHint(canonical_name="click", aliases=("pallets/click",)),)
    )
    router = build_router(trace, search_code, resolver)
    model_call = ToolCall(
        call_id="call-unknown-repository",
        tool_name=SEARCH_CODE,
        arguments={"query": "make_context", "repo": "unknown/click"},
    )

    result = asyncio.run(router.execute(model_call))

    recorded_call, recorded_result = trace.events
    assert invoked is False
    assert result.status == "error"
    assert result.error_type == ToolErrorCode.UNKNOWN_REPOSITORY.value
    assert recorded_call.arguments["repo"] == "unknown/click"
    assert recorded_result == result


def test_should_reject_ambiguous_repository_before_tool_execution() -> None:
    trace = TraceRecorder(task_id="router-ambiguous-repository")
    resolver = RepositoryAliasResolver(
        (
            RepositoryHint(canonical_name="click", aliases=("shared/click",)),
            RepositoryHint(canonical_name="click-fork", aliases=("shared/click",)),
        )
    )
    router = build_router(trace, lambda **_: {"matches": []}, resolver)
    model_call = ToolCall(
        call_id="call-ambiguous-repository",
        tool_name=SEARCH_CODE,
        arguments={"query": "make_context", "repo": "shared/click"},
    )

    result = asyncio.run(router.execute(model_call))

    assert result.status == "error"
    assert result.error_type == ToolErrorCode.AMBIGUOUS_REPOSITORY.value


def test_should_classify_blank_repository_as_invalid_arguments_before_resolution() -> None:
    trace = TraceRecorder(task_id="router-blank-repository")
    resolver = RepositoryAliasResolver((RepositoryHint(canonical_name="click"),))
    router = build_router(trace, lambda **_: {"matches": []}, resolver)
    model_call = ToolCall(
        call_id="call-blank-repository",
        tool_name=SEARCH_CODE,
        arguments={"query": "make_context", "repo": " "},
    )

    result = asyncio.run(router.execute(model_call))

    assert result.status == "error"
    assert result.error_type == ToolErrorCode.INVALID_ARGUMENTS.value
