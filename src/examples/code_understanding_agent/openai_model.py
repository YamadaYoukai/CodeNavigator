"""OpenAI Chat Completions adapter for one structured model decision.

The adapter deliberately has no trace dependency. It accepts an injected
OpenAI-compatible client, which keeps parsing tests offline and lets callers
configure credentials and transport outside this boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from pydantic import JsonValue, ValidationError

from .context import ModelInput, ToolSchema
from .model_boundary import (
    FinalAnswerDecision,
    ModelDecision,
    ToolCallDecision,
)
from .model_errors import InvalidModelOutputError, ModelExecutionError
from .tool_router import ALLOWED_TOOL_NAMES


_DECISION_PROTOCOL = """Return exactly one decision.
When retrieval is needed and remaining_tool_calls is positive, call exactly one
available retrieval tool. Never emit more than one tool call.
When answering without a tool call, return only a JSON object matching:
{"decision_type":"final_answer","answer":"non-empty answer",\
"evidence":["source references"],"uncertainties":[],"next_queries":[]}
Do not wrap the JSON in Markdown."""


def build_messages(model_input: ModelInput) -> list[dict[str, str]]:
    """Build deterministic messages without credentials or transport metadata."""

    if not isinstance(model_input, ModelInput):
        raise TypeError("model_input must be a ModelInput")

    user_payload = {
        "current_task": model_input.current_task,
        "evidence": [
            evidence.model_dump(mode="json") for evidence in model_input.evidence
        ],
        "remaining_tool_calls": model_input.remaining_tool_calls,
    }
    return [
        {
            "role": "system",
            "content": f"{model_input.system_instruction}\n\n{_DECISION_PROTOCOL}",
        },
        {
            "role": "user",
            "content": json.dumps(
                user_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]


def _strict_json_schema(value: JsonValue) -> JsonValue:
    """Convert a Pydantic schema to the strict function-calling subset."""

    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            # Pydantic emits these annotations for defaulted fields, but they
            # are not needed by the model-facing strict contract.
            if key in {"default", "title"}:
                continue
            normalized[key] = _strict_json_schema(item)

        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["required"] = list(properties)
            normalized["additionalProperties"] = False
        elif normalized.get("type") == "object":
            normalized["required"] = []
            normalized["additionalProperties"] = False
        return normalized

    if isinstance(value, list):
        return [_strict_json_schema(item) for item in value]
    return value


def build_tools(tool_schemas: Sequence[ToolSchema]) -> list[dict[str, Any]]:
    """Build strict Chat Completions function tools in allowlist order."""

    tools: list[dict[str, Any]] = []
    for tool_schema in tool_schemas:
        if not isinstance(tool_schema, ToolSchema):
            raise TypeError("tool_schemas must contain only ToolSchema instances")
        parameters = _strict_json_schema(
            deepcopy(tool_schema.input_schema)
        )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_schema.name,
                    "description": tool_schema.description,
                    "parameters": parameters,
                    "strict": True,
                },
            }
        )
    return tools


class OpenAIModel:
    """Adapt an injected OpenAI-compatible client to ``ModelClient``."""

    def __init__(self, *, client: Any, model: str) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def decide(self, model_input: ModelInput) -> ModelDecision:
        """Call Chat Completions once and return one validated decision."""

        if not isinstance(model_input, ModelInput):
            raise TypeError("model_input must be a ModelInput")

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=build_messages(model_input),
                tools=build_tools(model_input.tool_schemas),
                tool_choice="auto",
                parallel_tool_calls=False,
            )
        except Exception:
            raise ModelExecutionError() from None

        return _parse_response(response)


def _parse_response(response: object) -> ModelDecision:
    """Parse provider objects without retaining them in raised exceptions."""

    try:
        choices = getattr(response, "choices", None)
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
            raise InvalidModelOutputError()
        if len(choices) != 1:
            raise InvalidModelOutputError()

        message = getattr(choices[0], "message", None)
        if message is None or getattr(message, "refusal", None):
            raise InvalidModelOutputError()

        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            return _parse_tool_call(tool_calls)
        return _parse_final_answer(getattr(message, "content", None))
    except InvalidModelOutputError:
        raise InvalidModelOutputError() from None
    except Exception:
        raise InvalidModelOutputError() from None


def _parse_tool_call(tool_calls: object) -> ToolCallDecision:
    if not isinstance(tool_calls, Sequence) or isinstance(tool_calls, (str, bytes)):
        raise InvalidModelOutputError()
    if len(tool_calls) != 1:
        raise InvalidModelOutputError()

    tool_call = tool_calls[0]
    call_id = getattr(tool_call, "id", None)
    function = getattr(tool_call, "function", None)
    tool_name = getattr(function, "name", None)
    raw_arguments = getattr(function, "arguments", None)
    if not isinstance(call_id, str) or not call_id.strip():
        raise InvalidModelOutputError()
    if tool_name not in ALLOWED_TOOL_NAMES:
        raise InvalidModelOutputError()
    if not isinstance(raw_arguments, str):
        raise InvalidModelOutputError()

    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        raise InvalidModelOutputError() from None
    if not isinstance(arguments, Mapping):
        raise InvalidModelOutputError()

    try:
        return ToolCallDecision(
            call_id=call_id,
            tool_name=tool_name,
            arguments=dict(arguments),
        )
    except ValidationError:
        raise InvalidModelOutputError() from None


def _parse_final_answer(content: object) -> FinalAnswerDecision:
    if not isinstance(content, str) or not content.strip():
        raise InvalidModelOutputError()

    try:
        payload = json.loads(content)
        return FinalAnswerDecision.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError):
        raise InvalidModelOutputError() from None
