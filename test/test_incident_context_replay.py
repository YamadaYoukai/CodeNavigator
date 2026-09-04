import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

import evaluation.replay_incident_context as replay
from evaluation.replay_incident_context import (
    DEFAULT_FIXTURE_PATH,
    DEFAULT_SEARCH_ARTIFACT_PATH,
    EXPECTED_CONTEXT_CALL_ID,
    EXPECTED_CONTEXT_END_LINE,
    EXPECTED_CONTEXT_START_LINE,
    EXPECTED_LINES_AFTER,
    EXPECTED_LINES_BEFORE,
    FROZEN_SEARCH_ARTIFACT_SHA256,
    ContextEnvironmentPreflightError,
    ContextReplayValidationError,
    execute_replay,
    load_context_replay,
    preflight_environment,
    validate_execution_artifact,
)
from src.examples.code_understanding_agent import EvidenceKind, ToolCall, ToolResult


REAL_CONTEXT_ARTIFACT_PATH = (
    replay.PROJECT_ROOT
    / "evaluation/reports/incident-context-replay-real-2026-09-02.json"
)
FROZEN_REAL_CONTEXT_ARTIFACT_SHA256 = (
    "dae39f4618cb15d0f353db4d5c5bc131b524be2aa226970f1908b4f161646545"
)


def _search_artifact_payload() -> dict[str, object]:
    return json.loads(DEFAULT_SEARCH_ARTIFACT_PATH.read_text(encoding="utf-8"))


def _real_context_artifact_payload() -> dict[str, object]:
    return json.loads(REAL_CONTEXT_ARTIFACT_PATH.read_text(encoding="utf-8"))


def _encode_context_artifact(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _write_search_artifact(
    tmp_path: Path,
    payload: dict[str, object],
) -> tuple[Path, str]:
    path = tmp_path / DEFAULT_SEARCH_ARTIFACT_PATH.name
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.write_bytes(encoded)
    return path, hashlib.sha256(encoded).hexdigest()


def _context_content() -> str:
    lines: list[str] = []
    for line_number in range(
        EXPECTED_CONTEXT_START_LINE,
        EXPECTED_CONTEXT_END_LINE + 1,
    ):
        marker = ">" if line_number == replay.EXPECTED_GOLD_LINE else " "
        source = (
            f"                    {replay.EXPECTED_GOLD_SNIPPET}"
            if line_number == replay.EXPECTED_GOLD_LINE
            else f"synthetic line {line_number}"
        )
        lines.append(f"{marker}{line_number:5d} | {source}")
    return "\n".join(lines)


def _context_result(*, content: str | None = None) -> dict[str, object]:
    return {
        "repository": replay.EXPECTED_REPOSITORY,
        "file_path": replay.EXPECTED_GOLD_PATH,
        "target_line": replay.EXPECTED_GOLD_LINE,
        "start_line": EXPECTED_CONTEXT_START_LINE,
        "end_line": EXPECTED_CONTEXT_END_LINE,
        "total_lines": replay.EXPECTED_TOTAL_LINES,
        "content": content if content is not None else _context_content(),
        "truncated": True,
    }


def _allow_synthetic_context(monkeypatch: pytest.MonkeyPatch) -> str:
    content = _context_content()
    monkeypatch.setattr(
        replay,
        "EXPECTED_CONTEXT_CONTENT_SHA256",
        hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    return content


def _write_public_checkout(tmp_path: Path) -> tuple[Path, str]:
    repository_root = tmp_path / "repositories"
    source_path = repository_root / "click" / replay.EXPECTED_GOLD_PATH
    source_path.parent.mkdir(parents=True)
    source_lines = [
        f"synthetic line {line_number}"
        for line_number in range(1, replay.EXPECTED_TOTAL_LINES + 1)
    ]
    source_lines[replay.EXPECTED_GOLD_LINE - 1] = (
        f"                    {replay.EXPECTED_GOLD_SNIPPET}"
    )
    source_path.write_text("\n".join(source_lines) + "\n", encoding="utf-8")

    content_lines: list[str] = []
    for line_number in range(
        EXPECTED_CONTEXT_START_LINE,
        EXPECTED_CONTEXT_END_LINE + 1,
    ):
        marker = ">" if line_number == replay.EXPECTED_GOLD_LINE else " "
        content_lines.append(
            f"{marker}{line_number:5d} | {source_lines[line_number - 1]}"
        )
    content = "\n".join(content_lines)
    return repository_root, hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_frozen_search_artifact_builds_one_exact_context_decision() -> None:
    context = load_context_replay()

    assert context.fixture.fixture_sha256 == replay.FROZEN_FIXTURE_SHA256
    assert context.search_artifact_sha256 == FROZEN_SEARCH_ARTIFACT_SHA256
    assert context.decision.call_id == EXPECTED_CONTEXT_CALL_ID
    assert context.decision.tool_name == "get_file_context"
    assert context.decision.arguments == {
        "repository": "click",
        "file_path": "src/click/core.py",
        "line_number": 1284,
        "lines_before": 20,
        "lines_after": 20,
    }
    assert context.state.remaining_tool_calls == 1
    assert context.state.evidence_item_budget == 3
    assert len(context.state.evidence) == 2
    assert context.state.evidence[0] == context.fixture.state.evidence[0]
    assert context.state.evidence[1].kind is EvidenceKind.FACT
    assert context.state.evidence[1].source.startswith("artifact:search_code:")


def test_search_artifact_hash_drift_fails_before_derivation(tmp_path: Path) -> None:
    payload = _search_artifact_payload()
    payload["replay"]["repository"] = "other"  # type: ignore[index]
    path, _ = _write_search_artifact(tmp_path, payload)

    with pytest.raises(
        ContextReplayValidationError,
        match="search_artifact_hash_mismatch",
    ):
        load_context_replay(search_artifact_path=path)


@pytest.mark.parametrize(
    "mutation",
    [
        "case_id",
        "fixture_sha256",
        "revision",
        "repository",
        "query_sha256",
        "preflight",
        "calls",
        "budget",
        "trace",
        "tool_result_sha256",
        "rank",
        "gold_path",
        "state_check",
    ],
)
def test_semantic_upstream_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = deepcopy(_search_artifact_payload())
    preflight = payload["preflight"]  # type: ignore[assignment]
    result = payload["replay"]  # type: ignore[assignment]
    if mutation == "case_id":
        result["case_id"] = "other"
    elif mutation == "fixture_sha256":
        result["fixture_sha256"] = "0" * 64
    elif mutation == "revision":
        result["click_revision"] = "0" * 40
    elif mutation == "repository":
        result["repository"] = "other"
    elif mutation == "query_sha256":
        result["query_sha256"] = "0" * 64
    elif mutation == "preflight":
        preflight["checks"]["checkout_revision"] = False
    elif mutation == "calls":
        result["calls"]["search_code"] = 2
    elif mutation == "budget":
        result["budget"]["after"] = 1
    elif mutation == "trace":
        result["trace_event_types"] = ["tool_result", "tool_call"]
    elif mutation == "tool_result_sha256":
        result["tool_result_sha256"] = "0" * 64
    elif mutation == "rank":
        result["gold_match"]["rank"] = 2
    elif mutation == "gold_path":
        result["gold_match"]["path"] = "other.py"
    else:
        result["state_checks"]["incident_source_fact_preserved"] = False

    path, digest = _write_search_artifact(tmp_path, payload)
    monkeypatch.setattr(replay, "FROZEN_SEARCH_ARTIFACT_SHA256", digest)

    with pytest.raises(
        ContextReplayValidationError,
        match="invalid_search_artifact_contract",
    ):
        load_context_replay(search_artifact_path=path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rank", True),
        ("rank", 1.0),
        ("line", 1284.0),
    ],
)
def test_upstream_json_scalar_type_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload = deepcopy(_search_artifact_payload())
    payload["replay"]["gold_match"][field] = value  # type: ignore[index]
    path, digest = _write_search_artifact(tmp_path, payload)
    monkeypatch.setattr(replay, "FROZEN_SEARCH_ARTIFACT_SHA256", digest)

    with pytest.raises(
        ContextReplayValidationError,
        match="invalid_search_artifact_contract",
    ):
        load_context_replay(search_artifact_path=path)


@pytest.mark.parametrize("mutation", ["extra", "missing"])
def test_upstream_unknown_or_missing_fields_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = deepcopy(_search_artifact_payload())
    if mutation == "extra":
        payload["replay"]["unexpected"] = True  # type: ignore[index]
    else:
        del payload["replay"]["repository"]  # type: ignore[index]
    path, digest = _write_search_artifact(tmp_path, payload)
    monkeypatch.setattr(replay, "FROZEN_SEARCH_ARTIFACT_SHA256", digest)

    with pytest.raises(
        ContextReplayValidationError,
        match="invalid_search_artifact_contract",
    ):
        load_context_replay(search_artifact_path=path)


def test_fake_success_calls_only_context_once_and_preserves_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _allow_synthetic_context(monkeypatch)
    context = load_context_replay()
    search_invocations = 0
    context_invocations: list[dict[str, object]] = []

    def search_code(**_: object) -> object:
        nonlocal search_invocations
        search_invocations += 1
        raise AssertionError("search_code must not be called")

    def get_file_context(**arguments: object) -> dict[str, object]:
        context_invocations.append(arguments)
        return _context_result(content=content)

    execution = asyncio.run(
        execute_replay(
            context,
            get_file_context=get_file_context,
            search_code=search_code,
        )
    )

    assert search_invocations == 0
    assert context_invocations == [context.decision.arguments]
    assert [event.event_type for event in execution.trace.events] == [
        "tool_call",
        "tool_result",
    ]
    recorded_call, recorded_result = execution.trace.events
    assert isinstance(recorded_call, ToolCall)
    assert isinstance(recorded_result, ToolResult)
    assert recorded_call.tool_name == "get_file_context"
    assert execution.outcome.tool_result == recorded_result
    assert execution.outcome.next_state.remaining_tool_calls == 0
    assert execution.outcome.next_model_input.remaining_tool_calls == 0

    report = execution.report.model_dump(mode="json")
    assert report["status"] == "success"
    assert report["calls"] == {
        "search_code": 0,
        "get_file_context": 1,
        "model": 0,
        "agent_loop": 0,
    }
    assert report["budget"] == {"before": 1, "after": 0}
    assert report["trace_event_types"] == ["tool_call", "tool_result"]
    assert report["context_match"] == {
        "repository": "click",
        "file_path": "src/click/core.py",
        "target_line": 1284,
        "start_line": 1264,
        "end_line": 1304,
        "total_lines": 3542,
        "truncated": True,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    assert all(report["state_checks"].values())


def test_nested_decision_mutation_fails_before_any_tool_call() -> None:
    context = load_context_replay()
    context.decision.arguments["line_number"] = 1285
    context_invocations = 0

    def get_file_context(**_: object) -> object:
        nonlocal context_invocations
        context_invocations += 1
        return _context_result()

    with pytest.raises(
        ContextReplayValidationError,
        match="context_input_mutated_before_execution",
    ):
        asyncio.run(execute_replay(context, get_file_context=get_file_context))

    assert context_invocations == 0


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (RuntimeError("private backend detail"), "tool_execution_error"),
        (TimeoutError("private timeout detail"), "tool_timeout"),
    ],
)
def test_context_tool_failures_are_one_shot_and_sanitized(
    failure: Exception,
    expected_error: str,
) -> None:
    context = load_context_replay()
    invocations = 0

    def get_file_context(**_: object) -> object:
        nonlocal invocations
        invocations += 1
        raise failure

    execution = asyncio.run(
        execute_replay(context, get_file_context=get_file_context)
    )
    serialized = execution.report.model_dump_json()

    assert invocations == 1
    assert execution.report.status == "error"
    assert execution.report.error_type == expected_error
    assert execution.report.calls.search_code == 0
    assert execution.report.calls.get_file_context == 1
    assert execution.report.budget.after == 0
    assert "private backend detail" not in serialized
    assert "private timeout detail" not in serialized


@pytest.mark.parametrize(
    ("outcome", "expected_error"),
    [
        (object(), "tool_execution_error"),
        ({"unexpected": "shape"}, "unexpected_result"),
        (
            {
                **_context_result(),
                "target_line": 1285,
            },
            "context_mismatch",
        ),
        (
            {
                **_context_result(),
                "content": "wrong",
            },
            "context_mismatch",
        ),
    ],
)
def test_unexpected_context_outputs_stop_after_one_call(
    monkeypatch: pytest.MonkeyPatch,
    outcome: object,
    expected_error: str,
) -> None:
    _allow_synthetic_context(monkeypatch)
    context = load_context_replay()
    invocations = 0

    def get_file_context(**_: object) -> object:
        nonlocal invocations
        invocations += 1
        return outcome

    execution = asyncio.run(
        execute_replay(context, get_file_context=get_file_context)
    )

    assert invocations == 1
    assert execution.report.status == "error"
    assert execution.report.error_type == expected_error
    assert execution.report.calls.search_code == 0
    assert execution.report.calls.get_file_context == 1


def test_preflight_checks_only_local_pinned_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root, context_sha256 = _write_public_checkout(tmp_path)
    monkeypatch.setattr(
        replay,
        "EXPECTED_CONTEXT_CONTENT_SHA256",
        context_sha256,
    )

    def fake_git_output(_repository: Path, *arguments: str) -> str | None:
        if arguments == ("rev-parse", "HEAD"):
            return replay.PINNED_CLICK_REVISION
        if arguments == ("config", "--get", "zoekt.name"):
            return replay.EXPECTED_REPOSITORY
        raise AssertionError(arguments)

    monkeypatch.setattr(replay, "_git_output", fake_git_output)
    report = preflight_environment(
        load_context_replay(),
        repository_root=repository_root,
    )

    assert report.model_dump(mode="json") == {
        "status": "passed",
        "fixture_sha256": replay.FROZEN_FIXTURE_SHA256,
        "search_artifact_sha256": FROZEN_SEARCH_ARTIFACT_SHA256,
        "click_revision": replay.PINNED_CLICK_REVISION,
        "repository": "click",
        "context_arguments_sha256": replay._canonical_json_sha256(
            {
                "repository": "click",
                "file_path": "src/click/core.py",
                "line_number": 1284,
                "lines_before": 20,
                "lines_after": 20,
            }
        ),
        "checks": {
            "repository_root": True,
            "checkout_revision": True,
            "zoekt_name": True,
            "target_file": True,
            "target_line": True,
            "context_bounds": True,
            "context_content": True,
        },
    }


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("revision", "checkout_revision_mismatch"),
        ("name", "indexed_repository_name_mismatch"),
        ("line", "target_line_mismatch"),
        ("content", "context_content_mismatch"),
    ],
)
def test_preflight_drift_fails_before_a_tool_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
    expected: str,
) -> None:
    repository_root, context_sha256 = _write_public_checkout(tmp_path)
    if failure == "line":
        source = repository_root / "click" / replay.EXPECTED_GOLD_PATH
        lines = source.read_text(encoding="utf-8").splitlines()
        lines[replay.EXPECTED_GOLD_LINE - 1] = "wrong"
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        replay,
        "EXPECTED_CONTEXT_CONTENT_SHA256",
        "0" * 64 if failure == "content" else context_sha256,
    )

    def fake_git_output(_repository: Path, *arguments: str) -> str | None:
        if arguments == ("rev-parse", "HEAD"):
            return (
                "0" * 40
                if failure == "revision"
                else replay.PINNED_CLICK_REVISION
            )
        if arguments == ("config", "--get", "zoekt.name"):
            return "other" if failure == "name" else replay.EXPECTED_REPOSITORY
        raise AssertionError(arguments)

    monkeypatch.setattr(replay, "_git_output", fake_git_output)

    with pytest.raises(ContextEnvironmentPreflightError, match=expected):
        preflight_environment(
            load_context_replay(),
            repository_root=repository_root,
        )


def test_saved_execution_artifact_is_strictly_revalidated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    content = _allow_synthetic_context(monkeypatch)
    context_result = _context_result(content=content)
    monkeypatch.setattr(
        replay,
        "EXPECTED_CONTEXT_TOOL_RESULT_SHA256",
        replay._canonical_json_sha256(context_result),
    )
    context = load_context_replay()
    repository_root, _ = _write_public_checkout(tmp_path)

    def fake_git_output(_repository: Path, *arguments: str) -> str | None:
        if arguments == ("rev-parse", "HEAD"):
            return replay.PINNED_CLICK_REVISION
        if arguments == ("config", "--get", "zoekt.name"):
            return replay.EXPECTED_REPOSITORY
        raise AssertionError(arguments)

    monkeypatch.setattr(replay, "_git_output", fake_git_output)
    preflight = preflight_environment(context, repository_root=repository_root)
    execution = asyncio.run(
        execute_replay(
            context,
            get_file_context=lambda **_: context_result,
        )
    )
    artifact = replay.IncidentContextExecutionArtifact(
        preflight=preflight,
        replay=execution.report,
    )
    encoded = (
        json.dumps(
            artifact.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    validated = validate_execution_artifact(encoded, context)

    assert validated == artifact


def test_checked_in_real_context_artifact_recomputes_offline() -> None:
    encoded = REAL_CONTEXT_ARTIFACT_PATH.read_bytes()

    assert hashlib.sha256(encoded).hexdigest() == (
        FROZEN_REAL_CONTEXT_ARTIFACT_SHA256
    )
    artifact = validate_execution_artifact(encoded, load_context_replay())
    assert artifact.replay.status == "success"
    assert artifact.replay.calls.model_dump(mode="json") == {
        "search_code": 0,
        "get_file_context": 1,
        "model": 0,
        "agent_loop": 0,
    }
    assert artifact.replay.budget.model_dump(mode="json") == {
        "before": 1,
        "after": 0,
    }
    assert artifact.replay.trace_event_types == ("tool_call", "tool_result")
    assert artifact.replay.tool_result_sha256 == (
        replay.EXPECTED_CONTEXT_TOOL_RESULT_SHA256
    )
    assert artifact.replay.context_match is not None
    assert artifact.replay.context_match.content_sha256 == (
        replay.EXPECTED_CONTEXT_CONTENT_SHA256
    )


@pytest.mark.parametrize(
    "digest",
    [
        "not-a-digest",
        "0" * 64,
        "D6A654CC31E1EFD119F206DA9D949332AA8CC206F6D8449645D506DFDC337818",
    ],
    ids=["malformed", "wrong-well-formed", "uppercase"],
)
def test_saved_success_rejects_invalid_tool_result_digest(digest: str) -> None:
    payload = _real_context_artifact_payload()
    payload["replay"]["tool_result_sha256"] = digest  # type: ignore[index]

    with pytest.raises(
        ContextReplayValidationError,
        match="invalid_context_execution_artifact",
    ):
        validate_execution_artifact(
            _encode_context_artifact(payload),
            load_context_replay(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "fabricated_tool_error",
        "mismatched_tool_error",
        "semantic_error_with_tool_failure",
        "tool_error_with_successful_result",
        "fabricated_semantic_error",
    ],
)
def test_saved_failure_rejects_impossible_error_contracts(
    mutation: str,
) -> None:
    payload = _real_context_artifact_payload()
    report = payload["replay"]  # type: ignore[assignment]
    report["status"] = "error"
    report["context_match"] = None

    if mutation == "fabricated_tool_error":
        report["error_type"] = "fabricated_error"
        report["tool_result_status"] = "error"
        report["tool_result_error_type"] = "fabricated_error"
        report["tool_result_sha256"] = None
    elif mutation == "mismatched_tool_error":
        report["error_type"] = "tool_timeout"
        report["tool_result_status"] = "error"
        report["tool_result_error_type"] = "tool_execution_error"
        report["tool_result_sha256"] = None
    elif mutation == "semantic_error_with_tool_failure":
        report["error_type"] = "context_mismatch"
        report["tool_result_status"] = "error"
        report["tool_result_error_type"] = "context_mismatch"
        report["tool_result_sha256"] = None
    elif mutation == "tool_error_with_successful_result":
        report["error_type"] = "tool_timeout"
        report["tool_result_status"] = "success"
        report["tool_result_error_type"] = None
    else:
        report["error_type"] = "fabricated_error"
        report["tool_result_status"] = "success"
        report["tool_result_error_type"] = None

    with pytest.raises(
        ContextReplayValidationError,
        match="invalid_context_execution_artifact",
    ):
        validate_execution_artifact(
            _encode_context_artifact(payload),
            load_context_replay(),
        )


@pytest.mark.parametrize(
    ("tool_result_status", "error_type"),
    [
        ("error", "tool_execution_error"),
        ("error", "tool_timeout"),
        ("success", "unexpected_result"),
        ("success", "context_mismatch"),
    ],
)
def test_saved_failure_accepts_only_reachable_error_contracts(
    tool_result_status: str,
    error_type: str,
) -> None:
    payload = _real_context_artifact_payload()
    report = payload["replay"]  # type: ignore[assignment]
    report["status"] = "error"
    report["error_type"] = error_type
    report["tool_result_status"] = tool_result_status
    report["tool_result_error_type"] = (
        error_type if tool_result_status == "error" else None
    )
    report["tool_result_sha256"] = (
        None if tool_result_status == "error" else "a" * 64
    )
    report["context_match"] = None

    artifact = validate_execution_artifact(
        _encode_context_artifact(payload),
        load_context_replay(),
    )

    assert artifact.replay.status == "error"
    assert artifact.replay.error_type == error_type


def test_cli_missing_repository_root_stops_before_real_execution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("REPOSITORY_ROOT", raising=False)

    async def forbidden_execute(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real execution must remain blocked")

    monkeypatch.setattr(replay, "execute_replay", forbidden_execute)

    exit_code = replay.main(["--preflight-only"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "error",
        "error_type": "repository_root_missing",
    }
