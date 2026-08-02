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
from .events import (
    FinalAnswer,
    Session,
    Step,
    ToolCall,
    ToolResult,
    ModelRequest,
    ModelResult,
)
from .model_boundary import (
    FakeModel,
    FinalAnswerDecision,
    ModelClient,
    ModelDecision,
    ToolCallDecision,
)
from .model_errors import (
    InvalidModelOutputError,
    ModelBoundaryError,
    ModelExecutionError,
)
from .openai_model import OpenAIModel, build_messages, build_tools
from .trace import TraceRecorder
from .traced_model import TracedModelClient
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
    "FakeModel",
    "FinalAnswer",
    "FinalAnswerDecision",
    "ALLOWED_TOOL_NAMES",
    "GET_FILE_CONTEXT",
    "GetFileContextArguments",
    "InvalidModelOutputError",
    "ModelBoundaryError",
    "ModelClient",
    "ModelExecutionError",
    "ModelInput",
    "ModelDecision",
    "ModelRequest",
    "ModelResult",
    "OpenAIModel",
    "PydanticToolAdapter",
    "Router",
    "SEARCH_CODE",
    "SearchCodeArguments",
    "Session",
    "Step",
    "ToolDefinition",
    "ToolCall",
    "ToolCallDecision",
    "ToolErrorCode",
    "ToolRouter",
    "ToolSchema",
    "ToolResult",
    "ToolValidationError",
    "TraceRecorder",
    "TracedModelClient",
    "build_context",
    "build_messages",
    "build_tools",
]
