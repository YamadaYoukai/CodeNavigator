"""Trace, tool-routing, and deterministic-context blocks for the agent example."""

from .context import (
    ContextBuilder,
    ContextState,
    Evidence,
    EvidenceKind,
    ModelInput,
    ToolSchema,
    build_context,
)
from .events import FinalAnswer, Session, Step, ToolCall, ToolResult
from .trace import TraceRecorder
from .tool_router import (
    ALLOWED_TOOL_NAMES,
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    GetFileContextArguments,
    PydanticToolAdapter,
    Router,
    SearchCodeArguments,
    ToolDefinition,
    ToolErrorCode,
    ToolRouter,
    ToolValidationError,
)

__all__ = [
    "ContextBuilder",
    "ContextState",
    "Evidence",
    "EvidenceKind",
    "FinalAnswer",
    "ALLOWED_TOOL_NAMES",
    "GET_FILE_CONTEXT",
    "GetFileContextArguments",
    "ModelInput",
    "PydanticToolAdapter",
    "Router",
    "SEARCH_CODE",
    "SearchCodeArguments",
    "Session",
    "Step",
    "ToolDefinition",
    "ToolCall",
    "ToolErrorCode",
    "ToolRouter",
    "ToolSchema",
    "ToolResult",
    "ToolValidationError",
    "TraceRecorder",
    "build_context",
]
