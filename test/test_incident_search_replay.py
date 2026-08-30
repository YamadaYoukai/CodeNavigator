import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

import evaluation.replay_incident_search as replay
from evaluation.replay_incident_search import (
    DEFAULT_FIXTURE_PATH,
    FROZEN_FIXTURE_SHA256,
    PINNED_CLICK_REVISION,
    EnvironmentPreflightError,
    FixtureValidationError,
    execute_replay,
    load_fixture,
    preflight_environment,
)
from src.examples.code_understanding_agent import EvidenceKind, ToolCall, ToolResult


def _fixture_payload() -> dict[str, object]:
    return json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))


def _write_fixture(
    tmp_path: Path,
    payload: dict[str, object],
    *,
    checksum: str | None = None,
) -> Path:
    path = tmp_path / DEFAULT_FIXTURE_PATH.name
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    path.write_bytes(encoded)
    digest = checksum or hashlib.sha256(encoded).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n",
        encoding="utf-8",
    )
    return path


def _search_result(*, include_gold: bool = True) -> dict[str, object]:
    matches: list[dict[str, object]] = [
        {
            "repo": "click",
            "path": "src/click/core.py",
            "line": 1285,
            "snippet": '"Got unexpected extra arguments ({args})",',
        }
    ]
    if include_gold:
        matches.append(
            {
                "repo": "click",
                "path": "src/click/core.py",
                "line": 1284,
                "snippet": '"Got unexpected extra argument ({args})",',
            }
        )
    return {
        "query": "Got unexpected extra argument",
        "duration_ms": 1,
        "matches": matches,
    }


def test_frozen_fixture_builds_one_grounded_error_text_task() -> None:
    fixture = load_fixture(DEFAULT_FIXTURE_PATH)

    assert fixture.fixture_sha256 == FROZEN_FIXTURE_SHA256
    assert fixture.persisted.click_revision == PINNED_CLICK_REVISION
    assert fixture.extraction_result.error_text is not None
    assert fixture.extraction_result.error_text.value == (
        "Got unexpected extra argument"
    )
    assert fixture.extraction_result.exception_class is None
    assert fixture.extraction_result.method is None
    assert fixture.extraction_result.service_name is None
    assert fixture.extraction_result.configuration_keys == ()
    assert len(fixture.tasks) == 1
    assert fixture.task.field_name == "error_text"
    assert fixture.task.source_ids == ("log-001",)
    assert fixture.task.arguments.model_dump(mode="json") == {
        "query": "Got unexpected extra argument",
        "repo": "click",
        "lang": None,
        "path": None,
        "limit": 20,
        "literal": True,
    }
    assert fixture.decision.arguments == fixture.task.arguments.model_dump(
        mode="json"
    )
    assert fixture.state.remaining_tool_calls == 1


def test_fixture_hash_drift_fails_before_mapping(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["case_id"] = "changed"
    path = _write_fixture(
        tmp_path,
        payload,
        checksum=FROZEN_FIXTURE_SHA256,
    )

    with pytest.raises(FixtureValidationError, match="fixture_hash_mismatch"):
        load_fixture(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "source",
        "gold",
        "revision",
        "repository",
        "search_arguments",
    ],
)
def test_semantic_fixture_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = deepcopy(_fixture_payload())
    if mutation == "source":
        payload["incident_input"]["sources"][0]["text"] = "changed"  # type: ignore[index]
    elif mutation == "gold":
        payload["gold"]["line"] = 1285  # type: ignore[index]
    elif mutation == "revision":
        payload["click_revision"] = "0" * 40
    elif mutation == "repository":
        payload["selected_repository"] = "pallets/click"
    else:
        payload["search_arguments"]["literal"] = False  # type: ignore[index]

    path = _write_fixture(tmp_path, payload)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(replay, "FROZEN_FIXTURE_SHA256", digest)

    with pytest.raises(FixtureValidationError):
        load_fixture(path)


def test_fake_success_executes_exactly_once_and_preserves_incident_facts() -> None:
    fixture = load_fixture(DEFAULT_FIXTURE_PATH)
    search_invocations: list[dict[str, object]] = []
    context_invocations = 0

    async def search_code(**arguments: object) -> dict[str, object]:
        search_invocations.append(arguments)
        return _search_result()

    def get_file_context(**_: object) -> object:
        nonlocal context_invocations
        context_invocations += 1
        raise AssertionError("get_file_context must not be called")

    execution = asyncio.run(
        execute_replay(
            fixture,
            search_code=search_code,
            get_file_context=get_file_context,
        )
    )

    assert search_invocations == [fixture.task.arguments.model_dump(mode="python")]
    assert context_invocations == 0
    assert [event.event_type for event in execution.trace.events] == [
        "tool_call",
        "tool_result",
    ]
    recorded_call, recorded_result = execution.trace.events
    assert isinstance(recorded_call, ToolCall)
    assert isinstance(recorded_result, ToolResult)
    assert execution.outcome.tool_result == recorded_result
    assert execution.outcome.next_state.remaining_tool_calls == 0
    assert execution.outcome.next_model_input.remaining_tool_calls == 0

    original_source = fixture.state.evidence[0]
    assert original_source in execution.outcome.next_state.evidence
    assert original_source in execution.outcome.next_model_input.evidence
    fresh_fact = execution.outcome.next_state.evidence[0]
    assert fresh_fact.kind is EvidenceKind.FACT
    assert fresh_fact.source.startswith("tool_result:search_code:")
    assert fresh_fact in execution.outcome.next_model_input.evidence

    report = execution.report.model_dump(mode="json")
    assert report["status"] == "success"
    assert report["calls"] == {
        "search_code": 1,
        "get_file_context": 0,
        "model": 0,
        "agent_loop": 0,
    }
    assert report["budget"] == {"before": 1, "after": 0}
    assert report["trace_event_types"] == ["tool_call", "tool_result"]
    assert report["gold_match"]["rank"] == 2
    assert report["gold_match"]["repo"] == "click"
    assert report["gold_match"]["path"] == "src/click/core.py"
    assert report["gold_match"]["line"] == 1284
    assert "snippet" not in report["gold_match"]
    assert report["state_checks"] == {
        "new_tool_fact_in_next_state": True,
        "new_tool_fact_in_next_model_input": True,
        "incident_source_fact_preserved": True,
    }


def test_nested_decision_mutation_fails_before_a_tool_call() -> None:
    fixture = load_fixture(DEFAULT_FIXTURE_PATH)
    fixture.decision.arguments["repo"] = "other"
    invocations = 0

    def search_code(**_: object) -> object:
        nonlocal invocations
        invocations += 1
        return _search_result()

    with pytest.raises(
        FixtureValidationError,
        match="fixture_mutated_before_execution",
    ):
        asyncio.run(execute_replay(fixture, search_code=search_code))

    assert invocations == 0


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (RuntimeError("private backend detail"), "tool_execution_error"),
        (TimeoutError("private timeout detail"), "tool_timeout"),
    ],
)
def test_tool_failures_are_one_shot_and_sanitized(
    failure: Exception,
    expected_error: str,
) -> None:
    fixture = load_fixture(DEFAULT_FIXTURE_PATH)
    invocations = 0

    def search_code(**_: object) -> object:
        nonlocal invocations
        invocations += 1
        raise failure

    execution = asyncio.run(
        execute_replay(fixture, search_code=search_code)
    )
    serialized = execution.report.model_dump_json()

    assert invocations == 1
    assert execution.report.status == "error"
    assert execution.report.error_type == expected_error
    assert execution.report.calls.search_code == 1
    assert execution.report.budget.after == 0
    assert "private backend detail" not in serialized
    assert "private timeout detail" not in serialized


@pytest.mark.parametrize(
    ("outcome", "expected_error"),
    [
        (object(), "tool_execution_error"),
        ({"unexpected": "shape"}, "unexpected_result"),
        (_search_result(include_gold=False), "gold_not_found"),
    ],
)
def test_unexpected_outputs_stop_after_one_call(
    outcome: object,
    expected_error: str,
) -> None:
    fixture = load_fixture(DEFAULT_FIXTURE_PATH)
    invocations = 0

    def search_code(**_: object) -> object:
        nonlocal invocations
        invocations += 1
        return outcome

    execution = asyncio.run(
        execute_replay(fixture, search_code=search_code)
    )

    assert invocations == 1
    assert execution.report.status == "error"
    assert execution.report.error_type == expected_error
    assert execution.report.calls.search_code == 1
    assert execution.report.calls.get_file_context == 0


def test_preflight_checks_only_local_pinned_identity_and_gold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = load_fixture(DEFAULT_FIXTURE_PATH)

    def fake_git_output(_repository: Path, *arguments: str) -> str | None:
        if arguments == ("rev-parse", "HEAD"):
            return PINNED_CLICK_REVISION
        if arguments == ("config", "--get", "zoekt.name"):
            return "click"
        if arguments == (
            "show",
            f"{PINNED_CLICK_REVISION}:src/click/core.py",
        ):
            return "\n".join(
                ["unused"] * 1283
                + ['                    "Got unexpected extra argument ({args})",']
            )
        raise AssertionError(arguments)

    monkeypatch.setattr(replay, "_git_output", fake_git_output)

    report = preflight_environment(
        fixture,
        repository_root=tmp_path,
        declared_index_revision=PINNED_CLICK_REVISION,
        zoekt_url_configured=True,
    )

    assert report.model_dump(mode="json") == {
        "status": "passed",
        "fixture_sha256": FROZEN_FIXTURE_SHA256,
        "click_revision": PINNED_CLICK_REVISION,
        "repository": "click",
        "checks": {
            "zoekt_url_configured": True,
            "checkout_revision": True,
            "declared_index_revision": True,
            "zoekt_name": True,
            "gold_source_line": True,
        },
    }


@pytest.mark.parametrize(
    ("checkout", "declared", "name", "line", "expected"),
    [
        ("0" * 40, PINNED_CLICK_REVISION, "click", None, "checkout_revision_mismatch"),
        (PINNED_CLICK_REVISION, "0" * 40, "click", None, "index_revision_mismatch"),
        (PINNED_CLICK_REVISION, PINNED_CLICK_REVISION, "other", None, "indexed_repository_name_mismatch"),
        (PINNED_CLICK_REVISION, PINNED_CLICK_REVISION, "click", "wrong", "gold_source_mismatch"),
    ],
)
def test_preflight_fails_closed_without_a_tool_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    checkout: str,
    declared: str,
    name: str,
    line: str | None,
    expected: str,
) -> None:
    fixture = load_fixture(DEFAULT_FIXTURE_PATH)

    def fake_git_output(_repository: Path, *arguments: str) -> str | None:
        if arguments == ("rev-parse", "HEAD"):
            return checkout
        if arguments == ("config", "--get", "zoekt.name"):
            return name
        if arguments[0] == "show":
            return line
        raise AssertionError(arguments)

    monkeypatch.setattr(replay, "_git_output", fake_git_output)

    with pytest.raises(EnvironmentPreflightError, match=expected):
        preflight_environment(
            fixture,
            repository_root=tmp_path,
            declared_index_revision=declared,
            zoekt_url_configured=True,
        )


def test_cli_missing_environment_stops_before_real_execution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ZOEKT_URL", raising=False)
    monkeypatch.delenv("REPOSITORY_ROOT", raising=False)
    monkeypatch.delenv("ZOEKT_INDEX_REVISION", raising=False)

    async def forbidden_execute(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real execution must remain blocked")

    monkeypatch.setattr(replay, "execute_replay", forbidden_execute)

    exit_code = replay.main(
        [
            "--fixture",
            str(DEFAULT_FIXTURE_PATH),
            "--preflight-only",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "error",
        "error_type": "zoekt_url_missing",
    }
