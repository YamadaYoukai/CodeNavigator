"""Dependency-injected routing for the code-understanding agent tools.

The router owns the boundary between a trace ``ToolCall`` and a normalized
``ToolResult``.  Tool implementations are supplied as adapters so this module
has no dependency on an MCP client or on a particular service implementation.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from .events import ToolCall, ToolResult
from .trace import TraceRecorder


SEARCH_CODE = "search_code"
GET_FILE_CONTEXT = "get_file_context"
ALLOWED_TOOL_NAMES = frozenset({SEARCH_CODE, GET_FILE_CONTEXT})


class ToolErrorCode(str, Enum):
    """Stable error codes emitted by the router."""

    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    TOOL_EXECUTION_ERROR = "tool_execution_error"


class SearchCodeArguments(BaseModel):
    """Router-side contract for ``search_code``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    repo: str | None = None
    lang: str | None = None
    path: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    literal: bool = False


class GetFileContextArguments(BaseModel):
    """Router-side contract for ``get_file_context``."""

    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    line_number: int = Field(ge=1)
    lines_before: int = Field(default=20, ge=0, le=100)
    lines_after: int = Field(default=20, ge=0, le=100)


class ToolValidationError(ValueError):
    """Signals that an adapter rejected caller-supplied tool arguments."""


class ToolDefinition(Protocol):
    """Adapter contract injected into :class:`ToolRouter`.

    Implementations validate raw JSON-like arguments and invoke their backing
    tool.  They must raise ``ToolValidationError`` for invalid input; all other
    exceptions are classified as execution failures by the router.
    """

    def validate(self, arguments: Mapping[str, JsonValue]) -> object:
        """Return validated arguments or raise ``ToolValidationError``."""
        ...

    def invoke(self, arguments: object) -> object | Awaitable[object]:
        """Invoke the backing tool with validated arguments."""
        ...


class PydanticToolAdapter:
    """Adapt a callable and a Pydantic argument model to ``ToolDefinition``."""

    def __init__(
        self,
        arguments_model: type[BaseModel],
        handler: Callable[..., object],
    ) -> None:
        self._arguments_model = arguments_model
        self._handler = handler

    def validate(self, arguments: Mapping[str, JsonValue]) -> BaseModel:
        try:
            return self._arguments_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolValidationError("tool arguments do not match the contract") from exc

    async def invoke(self, arguments: object) -> object:
        if not isinstance(arguments, self._arguments_model):
            raise TypeError("validated arguments do not match the configured model")

        outcome = self._handler(**arguments.model_dump(mode="python"))
        if inspect.isawaitable(outcome):
            return await outcome
        return outcome


_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


class ToolRouter:
    """Validate, invoke, normalize, and trace the two supported tools."""

    def __init__(
        self,
        *,
        trace: TraceRecorder,
        tools: Mapping[str, ToolDefinition],
    ) -> None:
        if set(tools) != ALLOWED_TOOL_NAMES:
            raise ValueError(
                "tools must be configured for exactly search_code and get_file_context"
            )

        self._trace = trace
        self._tools = dict(tools)

    async def execute(self, call: ToolCall) -> ToolResult:
        """Record and execute one call, returning its normalized trace result."""

        self._trace.append(call)
        tool = self._tools.get(call.tool_name)
        if tool is None:
            return self._record_error(call.call_id, ToolErrorCode.UNKNOWN_TOOL)

        try:
            arguments = tool.validate(call.arguments)
        except (ToolValidationError, ValidationError):
            return self._record_error(call.call_id, ToolErrorCode.INVALID_ARGUMENTS)
        except Exception:
            return self._record_error(call.call_id, ToolErrorCode.TOOL_EXECUTION_ERROR)

        try:
            outcome = tool.invoke(arguments)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            result = _normalize_result(outcome)
        except Exception:
            return self._record_error(call.call_id, ToolErrorCode.TOOL_EXECUTION_ERROR)

        return self._record_success(call.call_id, result)

    def _record_success(self, call_id: str, result: JsonValue) -> ToolResult:
        return self._record_result(
            ToolResult(
                call_id=call_id,
                status="success",
                result=result,
            )
        )

    def _record_error(self, call_id: str, error_code: ToolErrorCode) -> ToolResult:
        return self._record_result(
            ToolResult(
                call_id=call_id,
                status="error",
                error_type=error_code.value,
            )
        )

    def _record_result(self, result: ToolResult) -> ToolResult:
        return cast(ToolResult, self._trace.append(result))


def _normalize_result(value: object) -> JsonValue:
    """Convert supported tool output to the JSON value stored in a trace."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")

    try:
        normalized = _JSON_VALUE_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise ValueError("tool result must be JSON-compatible") from exc

    if normalized is None:
        raise ValueError("a successful tool result must not be null")
    return normalized


# Keep the original short name available while making ToolRouter explicit in
# new call sites.
Router = ToolRouter
