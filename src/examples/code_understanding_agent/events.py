"""Event contracts for a code-understanding agent trace.

The event models deliberately contain no knowledge of MCP clients or concrete
tools. TraceRecorder supplies the task envelope (task_id and sequence) when an
event is recorded.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class TraceEvent(BaseModel):
    """Fields shared by every recorded event.

    task_id and sequence are intentionally unset while an event is being
    constructed. They are owned by TraceRecorder and are populated only when it
    appends the event to a trace.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str | None = Field(default=None, min_length=1)
    sequence: int | None = Field(default=None, ge=1)
    event_type: str
    elapsed_ms: int | None = Field(default=None, ge=0)


class Session(TraceEvent):
    """Describes the user task and the tools available for it."""

    event_type: Literal["session"] = "session"
    user_task: str = Field(min_length=1)
    available_tools: list[str] = Field(default_factory=list)


class Step(TraceEvent):
    """Records the purpose of one agent step."""

    event_type: Literal["step"] = "step"
    step_number: int = Field(ge=1)
    purpose: str = Field(min_length=1)


class ToolCall(TraceEvent):
    """Records a request to a tool without depending on its implementation."""

    event_type: Literal["tool_call"] = "tool_call"
    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(TraceEvent):
    """Records either the result of a tool call or its classified failure."""

    event_type: Literal["tool_result"] = "tool_result"
    call_id: str = Field(min_length=1)
    status: Literal["success", "error"]
    result: JsonValue | None = None
    error_type: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_outcome(self) -> "ToolResult":
        if self.status == "success":
            if self.result is None:
                raise ValueError("a successful tool result must include result")
            if self.error_type is not None:
                raise ValueError("a successful tool result cannot include error_type")
        else:
            if self.error_type is None:
                raise ValueError("an error tool result must include error_type")
            if self.result is not None:
                raise ValueError("an error tool result cannot include result")
        return self


class FinalAnswer(TraceEvent):
    """The single terminal answer for a trace."""

    event_type: Literal["final_answer"] = "final_answer"
    answer: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    next_queries: list[str] = Field(default_factory=list)
    termination_reason: str = Field(min_length=1)


class ModelRequest(TraceEvent):
    """Records the safe, provider-independent input to one model call."""

    event_type: Literal["model_request"] = "model_request"
    request_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_input: dict[str, JsonValue]


class ModelResult(TraceEvent):
    """Records one validated decision or one stable failure category."""

    event_type: Literal["model_result"] = "model_result"
    elapsed_ms: int = Field(ge=0)
    request_id: str = Field(min_length=1)
    status: Literal["success", "error"]
    decision: dict[str, JsonValue] | None = None
    error_type: Literal[
        "model_execution_error",
        "invalid_model_output",
    ] | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "ModelResult":
        if self.status == "success":
            if self.decision is None:
                raise ValueError("successful model result must include decision")
            if self.error_type is not None:
                raise ValueError("successful model result cannot include error_type")
        else:
            if self.error_type is None:
                raise ValueError("error model result must include error_type")
            if self.decision is not None:
                raise ValueError("error model result cannot include decision")
        return self


RecordedEvent: TypeAlias = (
    Session | Step | ToolCall | ToolResult | FinalAnswer | ModelRequest | ModelResult
)
NonTerminalEvent: TypeAlias = (
    Session | Step | ToolCall | ToolResult | ModelRequest | ModelResult
)

EVENT_MODELS: dict[str, type[TraceEvent]] = {
    "session": Session,
    "step": Step,
    "tool_call": ToolCall,
    "tool_result": ToolResult,
    "final_answer": FinalAnswer,
    "model_request": ModelRequest,
    "model_result": ModelResult,
}


def event_from_dict(data: Mapping[str, Any]) -> RecordedEvent:
    """Restore the concrete event model selected by its discriminator."""

    event_type = data.get("event_type")
    if not isinstance(event_type, str):
        raise ValueError("event_type must be a string")
    event_model = EVENT_MODELS.get(event_type)
    if event_model is None:
        raise ValueError(f"unknown event_type: {event_type!r}")
    return event_model.model_validate(dict(data))
