import asyncio
from collections.abc import Callable

from pydantic import BaseModel

from src.examples.code_understanding_agent import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    GetFileContextArguments,
    PydanticToolAdapter,
    SearchCodeArguments,
    ToolCall,
    ToolErrorCode,
    ToolResult,
    ToolRouter,
    TraceRecorder,
)


class SearchPayload(BaseModel):
    query: str
    matches: list[dict[str, str | int]]


def build_router(
    trace: TraceRecorder,
    search_handler: Callable[..., object],
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
