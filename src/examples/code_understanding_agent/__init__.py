"""Trace, tool-routing, and deterministic-context blocks for the agent example."""

from .agent_loop import AgentLoop, AgentLoopOutcome, MAX_TOOL_CALLS_PER_TASK
from .context import (
    ContextBuilder,
    ContextState,
    Evidence,
    EvidenceKind,
    ModelInput,
    RepositoryHint,
    ToolSchema,
    build_context,
)
from .events import (
    FinalAnswer,
    ModelRequest,
    ModelResult,
    Session,
    Step,
    ToolCall,
    ToolResult,
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
    ModelTimeoutError,
)
from .openai_model import OpenAIModel, build_messages, build_tools
from .repository_resolver import RepositoryAliasResolver
from .trace import TraceRecorder
from .traced_model import TracedModelClient
from .tool_step import (
    ToolCallExecutor,
    ToolStepInvariantError,
    ToolStepExecutor,
    ToolStepOutcome,
)
from .tool_router import (
    ALLOWED_TOOL_NAMES,
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    GetFileContextArguments,
    PydanticToolAdapter,
    Router,
    SearchCodeArguments,
    ToolCallResolutionError,
    ToolCallResolver,
    ToolDefinition,
    ToolErrorCode,
    ToolRouter,
    ToolValidationError,
)

__all__ = [
    "AgentLoop",
    "AgentLoopOutcome",
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
    "MAX_TOOL_CALLS_PER_TASK",
    "ModelBoundaryError",
    "ModelClient",
    "ModelExecutionError",
    "ModelTimeoutError",
    "ModelInput",
    "ModelDecision",
    "ModelRequest",
    "ModelResult",
    "OpenAIModel",
    "PydanticToolAdapter",
    "RepositoryAliasResolver",
    "RepositoryHint",
    "Router",
    "SEARCH_CODE",
    "SearchCodeArguments",
    "Session",
    "Step",
    "ToolCallResolutionError",
    "ToolCallResolver",
    "ToolCall",
    "ToolCallDecision",
    "ToolCallExecutor",
    "ToolDefinition",
    "ToolErrorCode",
    "ToolRouter",
    "ToolSchema",
    "ToolStepExecutor",
    "ToolStepInvariantError",
    "ToolStepOutcome",
    "ToolResult",
    "ToolValidationError",
    "TraceRecorder",
    "TracedModelClient",
    "build_context",
    "build_messages",
    "build_tools",
]
