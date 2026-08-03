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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from .events import ToolCall, ToolResult
from .trace import TraceRecorder


SEARCH_CODE = "search_code"
GET_FILE_CONTEXT = "get_file_context"
ALLOWED_TOOL_NAMES = frozenset({SEARCH_CODE, GET_FILE_CONTEXT})


class ToolErrorCode(str, Enum):
    """Stable error codes emitted by the router."""

    UNKNOWN_TOOL = "unknown_tool"
    UNKNOWN_REPOSITORY = "unknown_repository"
    AMBIGUOUS_REPOSITORY = "ambiguous_repository"
    INVALID_ARGUMENTS = "invalid_arguments"
    TOOL_EXECUTION_ERROR = "tool_execution_error"


class SearchCodeArguments(BaseModel):
    """Router-side contract for ``search_code``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    repo: str | None = Field(
        default=None,
        description=(
            "Exact canonical indexed repository name from repository_hints; "
            "use null when no supplied hint applies and never invent a name."
        ),
    )
    lang: str | None = None
    path: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    literal: bool = False

    @field_validator("repo")
    @classmethod
    def validate_repository_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("repo must be non-blank when supplied")
        return value


class GetFileContextArguments(BaseModel):
    """Router-side contract for ``get_file_context``."""

    model_config = ConfigDict(extra="forbid")

    repository: str = Field(
        min_length=1,
        description=(
            "Canonical repository name returned by search_code or supplied in "
            "repository_hints; never invent or rewrite it."
        ),
    )
    file_path: str = Field(min_length=1)
    line_number: int = Field(ge=1)
    lines_before: int = Field(default=20, ge=0, le=100)
    lines_after: int = Field(default=20, ge=0, le=100)

    @field_validator("repository")
    @classmethod
    def validate_repository_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("repository must be non-blank")
        return value


class ToolValidationError(ValueError):
    """Signals that an adapter rejected caller-supplied tool arguments."""


class ToolCallResolutionError(ValueError):
    """Reject a pre-execution call with one stable router error code."""

    def __init__(self, error_code: ToolErrorCode) -> None:
        if error_code not in {
            ToolErrorCode.UNKNOWN_REPOSITORY,
            ToolErrorCode.AMBIGUOUS_REPOSITORY,
        }:
            raise ValueError("unsupported tool call resolution error code")
        self.error_code = error_code
        super().__init__(error_code.value)


class ToolCallResolver(Protocol):
    """Resolve model-facing arguments before the executed call is traced."""

    def resolve(self, call: ToolCall) -> ToolCall:
        """Return an executable call or raise ToolCallResolutionError."""
        ...


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
        call_resolver: ToolCallResolver | None = None,
    ) -> None:
        if set(tools) != ALLOWED_TOOL_NAMES:
            raise ValueError(
                "tools must be configured for exactly search_code and get_file_context"
            )

        self._trace = trace
        self._tools = dict(tools)
        self._call_resolver = call_resolver

    async def execute(self, call: ToolCall) -> ToolResult:
        """Record and execute one call, returning its normalized trace result."""

        executable_call, resolution_error = self._resolve_call(call)
        self._trace.append(executable_call)
        if resolution_error is not None:
            return self._record_error(call.call_id, resolution_error)

        tool = self._tools.get(executable_call.tool_name)
        if tool is None:
            return self._record_error(
                executable_call.call_id,
                ToolErrorCode.UNKNOWN_TOOL,
            )

        try:
            arguments = tool.validate(executable_call.arguments)
        except (ToolValidationError, ValidationError):
            return self._record_error(
                executable_call.call_id,
                ToolErrorCode.INVALID_ARGUMENTS,
            )
        except Exception:
            return self._record_error(
                executable_call.call_id,
                ToolErrorCode.TOOL_EXECUTION_ERROR,
            )

        try:
            outcome = tool.invoke(arguments)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            result = _normalize_result(outcome)
        except Exception:
            return self._record_error(
                executable_call.call_id,
                ToolErrorCode.TOOL_EXECUTION_ERROR,
            )

        return self._record_success(executable_call.call_id, result)

    def _resolve_call(
        self,
        call: ToolCall,
    ) -> tuple[ToolCall, ToolErrorCode | None]:
        if self._call_resolver is None:
            return call.model_copy(deep=True), None

        try:
            resolved = self._call_resolver.resolve(call.model_copy(deep=True))
        except ToolCallResolutionError as exc:
            return call.model_copy(deep=True), exc.error_code
        except Exception:
            return call.model_copy(deep=True), ToolErrorCode.TOOL_EXECUTION_ERROR

        if not isinstance(resolved, ToolCall):
            return call.model_copy(deep=True), ToolErrorCode.TOOL_EXECUTION_ERROR
        if (
            resolved.call_id != call.call_id
            or resolved.tool_name != call.tool_name
            or resolved.task_id is not None
            or resolved.sequence is not None
        ):
            return call.model_copy(deep=True), ToolErrorCode.TOOL_EXECUTION_ERROR
        return resolved.model_copy(deep=True), None

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
