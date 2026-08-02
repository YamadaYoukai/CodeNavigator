#!/usr/bin/env python3
"""Run one real model decision without invoking a retrieval tool."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse

from openai import OpenAI

from src.examples.code_understanding_agent import (
    ContextState,
    ModelRequest,
    ModelResult,
    SEARCH_CODE,
    SearchCodeArguments,
    Session,
    Step,
    ToolCallDecision,
    TraceRecorder,
    TracedModelClient,
    OpenAIModel,
    build_context,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TRACE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "base_url",
        "default_headers",
        "headers",
        "raw_exception",
        "raw_response",
    }
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one real, redaction-checked model decision."
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON evidence path (relative paths use the project root).",
    )
    parser.add_argument(
        "--no-proxy-base-url",
        action="store_true",
        help=(
            "Append the configured API host to NO_PROXY/no_proxy before "
            "constructing the SDK client."
        ),
    )
    return parser.parse_args()


def _contains_forbidden_trace_key(value: object) -> bool:
    if isinstance(value, dict):
        if any(str(key).lower() in FORBIDDEN_TRACE_KEYS for key in value):
            return True
        return any(_contains_forbidden_trace_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_trace_key(item) for item in value)
    return False


def _resolve_output_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def configure_no_proxy_for_base_url(base_url: str) -> None:
    """Bypass process-level HTTP/HTTPS/SOCKS proxies for one API host."""

    hostname = urlparse(base_url).hostname
    if hostname is None or not hostname.strip():
        raise RuntimeError("OPENAI_BASE_URL must include a hostname")

    for variable_name in ("NO_PROXY", "no_proxy"):
        existing = [
            entry.strip()
            for entry in os.environ.get(variable_name, "").split(",")
            if entry.strip()
        ]
        if hostname not in existing:
            existing.append(hostname)
        os.environ[variable_name] = ",".join(existing)


def _missing_environment(name: str) -> NoReturn:
    raise RuntimeError(f"required environment variable is not configured: {name}")


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        _missing_environment(name)
    return value


def main() -> None:
    args = _parse_args()
    api_key = _required_environment("OPENAI_API_KEY")
    base_url = _required_environment("OPENAI_BASE_URL")
    model = _required_environment("OPENAI_MODEL")
    if args.no_proxy_base_url:
        configure_no_proxy_for_base_url(base_url)

    state = ContextState(
        system_instruction=(
            "Answer only from supplied code evidence. "
            "When evidence is insufficient and tool budget remains, "
            "call exactly one retrieval tool. Do not answer from memory."
        ),
        current_task=(
            "Where is make_context implemented in Click, "
            "and what does it do?"
        ),
        evidence=(),
        evidence_item_budget=4,
        remaining_tool_calls=1,
    )
    model_input = build_context(state)
    trace = TraceRecorder()
    trace.append(
        Session(
            user_task=state.current_task,
            available_tools=["search_code", "get_file_context"],
        )
    )
    trace.append(
        Step(
            step_number=1,
            purpose="Request one evidence-aware model decision.",
        )
    )

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=60.0,
    )
    model_client = TracedModelClient(
        client=OpenAIModel(client=client, model=model),
        model=model,
        trace=trace,
    )

    decision = model_client.decide(model_input)

    assert isinstance(decision, ToolCallDecision)
    assert decision.tool_name == SEARCH_CODE
    SearchCodeArguments.model_validate(decision.arguments)
    assert [event.event_type for event in trace.events] == [
        "session",
        "step",
        "model_request",
        "model_result",
    ]
    request = trace.events[2]
    result = trace.events[3]
    assert isinstance(request, ModelRequest)
    assert isinstance(result, ModelResult)
    assert request.request_id == result.request_id
    assert decision.call_id != request.request_id
    assert result.elapsed_ms >= 0
    assert trace.is_finalized is False

    trace_payload = trace.to_dict()
    serialized_trace = json.dumps(
        trace_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    redaction_evidence = {
        "configured_secret_values_absent": all(
            sensitive_value not in serialized_trace
            for sensitive_value in (api_key, base_url)
        ),
        "transport_and_raw_provider_fields_absent": not _contains_forbidden_trace_key(
            trace_payload
        ),
    }
    for sensitive_value in (api_key, base_url):
        assert sensitive_value not in serialized_trace
    assert all(redaction_evidence.values())

    report = {
        "model_input": model_input.to_payload(),
        "decision": decision.model_dump(mode="json"),
        "trace": trace_payload,
        "model_elapsed_ms": result.elapsed_ms,
        "redaction_evidence": redaction_evidence,
    }
    serialized_report = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    for sensitive_value in (api_key, base_url):
        assert sensitive_value not in serialized_report

    if args.out is None:
        print(serialized_report)
        return

    output_path = _resolve_output_path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{serialized_report}\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "decision_type": decision.decision_type,
                "model_elapsed_ms": result.elapsed_ms,
                "redaction_evidence": redaction_evidence,
                "report_path": str(output_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
