"""An in-memory recorder for deterministic code-understanding agent traces."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from .events import (
    FinalAnswer,
    ModelRequest,
    ModelResult,
    NonTerminalEvent,
    RecordedEvent,
    Session,
    Step,
    ToolCall,
    ToolResult,
    TraceEvent,
    event_from_dict,
)


class TraceRecorder:
    """Assign trace metadata and enforce the trace terminal-state invariants."""

    def __init__(self, task_id: str | None = None) -> None:
        if task_id is None:
            task_id = str(uuid4())
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")

        self._task_id = task_id
        self._events: list[RecordedEvent] = []
        self._finalized = False

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def events(self) -> tuple[RecordedEvent, ...]:
        """Return snapshots so callers cannot mutate the stored trace."""

        return tuple(event.model_copy(deep=True) for event in self._events)

    @property
    def is_finalized(self) -> bool:
        return self._finalized

    def append(self, event: NonTerminalEvent) -> RecordedEvent:
        """Append a non-terminal event and assign its task metadata."""

        self._ensure_appendable()
        if not isinstance(event, TraceEvent):
            raise TypeError("event must be a trace event")
        if isinstance(event, FinalAnswer):
            raise ValueError("use finalize() to record a FinalAnswer")
        if not isinstance(
            event,
            (Session, Step, ToolCall, ToolResult, ModelRequest, ModelResult),
        ):
            raise TypeError("event must be one of the supported non-terminal events")
        return self._record(event)

    def finalize(self, event: FinalAnswer) -> FinalAnswer:
        """Record the only terminal event and close the trace."""

        self._ensure_appendable()
        if not isinstance(event, FinalAnswer):
            raise TypeError("finalize() requires a FinalAnswer")
        recorded = self._record(event)
        self._finalized = True
        return recorded

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation of the complete trace."""

        return {
            "task_id": self.task_id,
            "events": [event.model_dump(mode="json") for event in self._events],
            "finalized": self.is_finalized,
        }

    def to_json(self) -> str:
        """Serialize deterministically without adding a wall-clock timestamp."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, payload: str) -> "TraceRecorder":
        """Deserialize a trace while validating type, order, and terminal state."""

        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("trace JSON must encode an object")
        return cls.from_dict(decoded)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TraceRecorder":
        """Restore a trace previously emitted by to_dict."""

        task_id = payload.get("task_id")
        events_payload = payload.get("events")
        finalized = payload.get("finalized")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("serialized trace must have a non-empty task_id")
        if not isinstance(events_payload, list):
            raise ValueError("serialized trace events must be a list")
        if not isinstance(finalized, bool):
            raise ValueError("serialized trace finalized must be a boolean")

        trace = cls(task_id=task_id)
        for expected_sequence, raw_event in enumerate(events_payload, start=1):
            if not isinstance(raw_event, Mapping):
                raise ValueError("serialized trace events must be objects")
            event = event_from_dict(raw_event)
            if event.task_id != trace.task_id:
                raise ValueError("serialized event task_id does not match trace task_id")
            if event.sequence != expected_sequence:
                raise ValueError("serialized event sequence is not continuous")
            if trace._finalized:
                raise ValueError("serialized trace contains an event after FinalAnswer")

            trace._events.append(event)
            if isinstance(event, FinalAnswer):
                trace._finalized = True

        if finalized != trace._finalized:
            raise ValueError("serialized trace finalized state does not match its events")
        return trace

    def _ensure_appendable(self) -> None:
        if self._finalized:
            raise RuntimeError("trace has already been finalized")

    def _record(self, event: TraceEvent) -> RecordedEvent:
        if event.task_id is not None:
            raise ValueError("task_id is managed by TraceRecorder")
        if event.sequence is not None:
            raise ValueError("sequence is managed by TraceRecorder")

        recorded = event.model_copy(
            update={
                "task_id": self.task_id,
                "sequence": len(self._events) + 1,
            },
            deep=True,
        )
        self._events.append(recorded)
        return recorded
