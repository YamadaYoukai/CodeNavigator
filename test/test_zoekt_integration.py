"""Opt-in, real Zoekt regression test.

Run it only against a running Zoekt instance that indexes the local
``../repos/fulfillment`` checkout:

    RUN_ZOEKT_INTEGRATION=1 python -m pytest -q -m integration

The report is written to ``.artifacts/zoekt-integration.json`` by default. Set
``ZOEKT_INTEGRATION_REPORT`` to place it elsewhere.
"""

import asyncio
import html
import json
import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
import pytest

import src.server as server
from src.services.zoekt_client import ZoektClient


pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES_ROOT = PROJECT_ROOT.parent / "repos"

# Keep this baseline deliberately narrow and immutable so an index/source drift
# is caught as a deterministic integration failure instead of a flaky search.
TARGET_REPOSITORY = "fulfillment"
TARGET_FILE_PATH = (
    "wallet-fulfillment-handler/src/main/java/com/xiaoju/wallet/"
    "fulfillment/handler/FulfillmentBaseBizHandler.java"
)
TARGET_SEARCH_QUERY = "fulfillProcessDecision"
TARGET_CODE_FRAGMENT = (
    "fulfillmentBaseService.fulfillProcessDecision("
    "fulfillmentBaseContext.getFulfillmentBaseDO());"
)
TARGET_CHINESE_QUERY = "履约处理决策"
INTEGRATION_ENABLED_ENV = "RUN_ZOEKT_INTEGRATION"
DEFAULT_ZOEKT_URL = "http://localhost:6070"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _require_explicit_enablement() -> None:
    if os.getenv(INTEGRATION_ENABLED_ENV) != "1":
        pytest.skip(
            "real Zoekt integration is disabled; set "
            f"{INTEGRATION_ENABLED_ENV}=1 and run `python -m pytest -q "
            "-m integration`"
        )


def _report_path() -> Path:
    configured_path = os.getenv("ZOEKT_INTEGRATION_REPORT")
    path = (
        Path(configured_path).expanduser()
        if configured_path
        else PROJECT_ROOT / ".artifacts" / "zoekt-integration.json"
    )
    return path if path.is_absolute() else PROJECT_ROOT / path


def _write_report(report: dict[str, Any]) -> None:
    path = _report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_git(repository_root: Path, *arguments: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repository_root), *arguments],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _local_repository_commit(repository_root: Path) -> str | None:
    return _run_git(repository_root, "rev-parse", "HEAD")


def _server_commit(zoekt_url: str) -> dict[str, str | None]:
    configured_commit = os.getenv("ZOEKT_SERVER_COMMIT")
    if configured_commit:
        return {"value": configured_commit, "source": "ZOEKT_SERVER_COMMIT"}

    try:
        response = httpx.get(f"{zoekt_url}/about", timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return {"value": None, "source": f"about unavailable: {exc}"}

    match = re.search(
        r"Zoekt version\s+([^,<]+)",
        html.unescape(response.text),
        flags=re.IGNORECASE,
    )
    if match:
        return {"value": match.group(1).strip(), "source": "/about"}
    return {"value": None, "source": "not exposed by /about"}


def _indexed_repository_metadata(zoekt_url: str) -> dict[str, Any]:
    """Read the indexed repository commit from Zoekt's JSON list API."""
    response = httpx.post(
        f"{zoekt_url}/api/list",
        json={"Q": f"r:^{TARGET_REPOSITORY}$"},
        timeout=10.0,
    )
    response.raise_for_status()

    payload = response.json()
    repositories = (payload.get("List") or {}).get("Repos") or []
    indexed_repository = next(
        (
            repository
            for repository in repositories
            if (repository.get("Repository") or {}).get("Name")
            == TARGET_REPOSITORY
        ),
        None,
    )
    assert indexed_repository is not None, (
        f"Zoekt /api/list does not contain indexed repository "
        f"{TARGET_REPOSITORY!r}"
    )

    repository = indexed_repository.get("Repository") or {}
    branches = repository.get("Branches") or []
    head = next(
        (branch for branch in branches if branch.get("Name") == "HEAD"),
        branches[0] if branches else {},
    )
    return {
        "name": repository.get("Name"),
        "commit": head.get("Version"),
        "branches": [
            {"name": branch.get("Name"), "commit": branch.get("Version")}
            for branch in branches
        ],
        "index_time": (indexed_repository.get("IndexMetadata") or {}).get(
            "IndexTime"
        ),
    }


def _target_source_lines(source_file: Path) -> list[str]:
    assert source_file.is_file(), (
        "integration baseline source file is missing: "
        f"{source_file}. Ensure ../repos/fulfillment is checked out."
    )
    return source_file.read_text(encoding="utf-8").splitlines()


def _target_match(matches: list[Any], source_lines: list[str]) -> Any:
    assert matches, (
        "search_code returned no matches for the fixed target query "
        f"{TARGET_SEARCH_QUERY!r}"
    )

    file_matches = [
        match
        for match in matches
        if match.repo == TARGET_REPOSITORY and match.path == TARGET_FILE_PATH
    ]
    assert file_matches, (
        "search_code did not return the fixed repository/path: "
        f"expected=({TARGET_REPOSITORY!r}, {TARGET_FILE_PATH!r}), "
        f"actual={[(match.repo, match.path) for match in matches]}"
    )

    line_matches = [
        match
        for match in file_matches
        if 1 <= match.line <= len(source_lines)
        and TARGET_CODE_FRAGMENT in source_lines[match.line - 1]
    ]
    assert line_matches, (
        "search_code returned the expected file but no line containing the "
        f"fixed target fragment {TARGET_CODE_FRAGMENT!r}: "
        f"{[(match.line, match.snippet) for match in file_matches]}"
    )
    return line_matches[0]


def _match_summaries(matches: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "repo": match.repo,
            "path": match.path,
            "line": match.line,
            "snippet": match.snippet,
        }
        for match in matches
    ]


def _assert_unicode_query(source_lines: list[str]) -> dict[str, Any]:
    """Exercise a Chinese literal query through the exposed search_code tool."""
    response = asyncio.run(
        server.search_code(
            query=TARGET_CHINESE_QUERY,
            repo=TARGET_REPOSITORY,
            lang="java",
            limit=100,
            literal=True,
        )
    )
    matching_hits = [
        match
        for match in response.matches
        if match.repo == TARGET_REPOSITORY
        and match.path == TARGET_FILE_PATH
        and 1 <= match.line <= len(source_lines)
        and TARGET_CHINESE_QUERY in source_lines[match.line - 1]
    ]
    assert matching_hits, (
        "Chinese literal query did not return a line that agrees with the "
        f"local source: {TARGET_CHINESE_QUERY!r}"
    )
    hit = matching_hits[0]
    return {
        "query": TARGET_CHINESE_QUERY,
        "literal": True,
        "repo": hit.repo,
        "path": hit.path,
        "line": hit.line,
        "local_source_line": source_lines[hit.line - 1],
    }


def test_search_code_to_file_context_matches_local_source(monkeypatch: pytest.MonkeyPatch):
    """Exercise search_code -> repo/path/line -> get_file_context end to end."""
    _require_explicit_enablement()

    started_at = _utc_now()
    started_monotonic = perf_counter()
    zoekt_url = os.getenv("ZOEKT_URL", DEFAULT_ZOEKT_URL).rstrip("/")
    repository_root = REPOSITORIES_ROOT / TARGET_REPOSITORY
    source_file = repository_root / TARGET_FILE_PATH
    server_commit = _server_commit(zoekt_url)
    report: dict[str, Any] = {
        "test": "test_search_code_to_file_context_matches_local_source",
        "started_at": _utc_timestamp(started_at),
        "zoekt": {
            "url": zoekt_url,
            "client": {
                "implementation": "src.services.zoekt_client.ZoektClient",
                "httpx_version": httpx.__version__,
                "python": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "python_executable": sys.executable,
                "timeout_seconds": 10.0,
            },
            "server_commit": server_commit["value"],
            "server_commit_source": server_commit["source"],
        },
        "repository": {
            "name": TARGET_REPOSITORY,
            "local_checkout_commit": _local_repository_commit(repository_root),
            "target_file": TARGET_FILE_PATH,
            "target_fragment": TARGET_CODE_FRAGMENT,
        },
        "execution": {"result": "running"},
    }

    try:
        source_lines = _target_source_lines(source_file)
        indexed_repository = _indexed_repository_metadata(zoekt_url)
        report["repository"]["indexed_repository_commit"] = (
            indexed_repository["commit"]
        )
        report["repository"]["indexed_repository"] = indexed_repository

        # Call the MCP tool implementation, not ZoektClient directly, for the
        # primary regression path. The monkeypatch makes its dependency explicit
        # while preserving the normal server configuration in every other test.
        monkeypatch.setenv("REPOSITORY_ROOT", str(REPOSITORIES_ROOT))
        monkeypatch.setattr(
            server,
            "zoekt_client",
            ZoektClient(base_url=zoekt_url, timeout=10.0),
        )
        search_result = asyncio.run(
            server.search_code(
                query=TARGET_SEARCH_QUERY,
                repo=TARGET_REPOSITORY,
                lang="java",
                limit=100,
                literal=True,
            )
        )
        report["search_code"] = {
            "query": TARGET_SEARCH_QUERY,
            "literal": True,
            "returned_matches": _match_summaries(search_result.matches),
        }
        match = _target_match(search_result.matches, source_lines)

        assert 1 <= match.line <= len(source_lines), (
            f"Zoekt returned invalid line {match.line} for {TARGET_FILE_PATH}; "
            f"local file has {len(source_lines)} lines"
        )
        local_source_line = source_lines[match.line - 1]
        assert TARGET_CODE_FRAGMENT in local_source_line, (
            "Zoekt's repo/path/line does not identify the local target fragment: "
            f"line {match.line} is {local_source_line!r}"
        )
        assert match.snippet == local_source_line.strip(), (
            "Zoekt's returned snippet differs from the local source line: "
            f"zoekt={match.snippet!r}, local={local_source_line.strip()!r}"
        )

        context = server.get_file_context(
            repository=match.repo,
            file_path=match.path,
            line_number=match.line,
            lines_before=1,
            lines_after=1,
        )
        assert context.repository == TARGET_REPOSITORY
        assert context.file_path == TARGET_FILE_PATH
        assert context.target_line == match.line
        assert TARGET_CODE_FRAGMENT in context.content

        report["search_code"].update(
            {
                "repo": match.repo,
                "path": match.path,
                "line": match.line,
                "snippet": match.snippet,
                "local_source_line": local_source_line,
            }
        )
        report["get_file_context"] = {
            "target_line": context.target_line,
            "start_line": context.start_line,
            "end_line": context.end_line,
            "contains_target_fragment": TARGET_CODE_FRAGMENT in context.content,
        }
        report["unicode_query"] = _assert_unicode_query(source_lines)
        report["execution"]["result"] = "passed"
    except BaseException as exc:
        report["execution"]["result"] = "failed"
        report["execution"]["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        finished_at = _utc_now()
        report["finished_at"] = _utc_timestamp(finished_at)
        report["execution"]["duration_ms"] = round(
            (perf_counter() - started_monotonic) * 1000,
            3,
        )
        report["report_path"] = str(_report_path())
        _write_report(report)
