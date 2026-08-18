#!/usr/bin/env python3
"""Validate or run the independently frozen Click 8.4.1 holdout once."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation import run_agent_eval as agent_eval


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOLDOUT_CASES_PATH = (
    PROJECT_ROOT / "evaluation/agent_holdout_cases_2026-08-19.jsonl"
)
DEFAULT_EXCLUSION_SET_PATH = (
    PROJECT_ROOT
    / "evaluation/fixtures/agent-holdout-exclusion-set-2026-08-19.json"
)
FROZEN_HOLDOUT_DATASET_SHA256 = (
    "e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396"
)
FROZEN_EXCLUSION_SET_SHA256 = (
    "716e244d016bfe9a53e1b01410faeef26db7d526ef1d6905c24dbca6e8623e3e"
)
EXPECTED_EXCLUSION_COUNTS = {
    "agent_cases": 10,
    "retrieval_cases": 18,
    "range_citation_failures": 6,
}


class AgentHoldoutDataError(agent_eval.AgentEvalDataError):
    """Signal a stable holdout or exclusion-set validation failure."""


def _load_exclusion_set(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AgentHoldoutDataError("holdout exclusion set does not exist")
    if agent_eval.dataset_sha256(path) != FROZEN_EXCLUSION_SET_SHA256:
        raise AgentHoldoutDataError("holdout exclusion set hash does not match freeze")

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentHoldoutDataError("holdout exclusion set is invalid") from exc

    if not isinstance(document, dict):
        raise AgentHoldoutDataError("holdout exclusion set is invalid")
    if document.get("click_revision") != agent_eval.PINNED_CLICK_REVISION:
        raise AgentHoldoutDataError("holdout exclusion revision does not match")
    sources = document.get("sources")
    if not isinstance(sources, list):
        raise AgentHoldoutDataError("holdout exclusion sources are invalid")

    observed_counts: dict[str, int] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise AgentHoldoutDataError("holdout exclusion source is invalid")
        kind = source.get("kind")
        cases = source.get("cases")
        if not isinstance(kind, str) or not isinstance(cases, list):
            raise AgentHoldoutDataError("holdout exclusion source is invalid")
        if source.get("case_count") != len(cases):
            raise AgentHoldoutDataError("holdout exclusion source count changed")
        observed_counts[kind] = len(cases)

        source_path_value = source.get("path")
        source_sha256 = source.get("sha256")
        if not isinstance(source_path_value, str) or not isinstance(source_sha256, str):
            raise AgentHoldoutDataError("holdout exclusion source freeze is invalid")
        source_path = agent_eval.resolve_project_path(Path(source_path_value))
        if not source_path.is_file():
            raise AgentHoldoutDataError("holdout exclusion source does not exist")
        if agent_eval.dataset_sha256(source_path) != source_sha256:
            raise AgentHoldoutDataError("holdout exclusion source hash changed")

    if observed_counts != EXPECTED_EXCLUSION_COUNTS:
        raise AgentHoldoutDataError("holdout exclusion coverage is incomplete")
    return document


def validate_holdout_disjointness(
    cases: Sequence[agent_eval.AgentEvalCase], exclusion_set: dict[str, Any]
) -> None:
    excluded_ids: set[str] = set()
    excluded_prompts: set[str] = set()
    excluded_locations: list[tuple[str, str, int, int]] = []

    for source in exclusion_set["sources"]:
        for excluded_case in source["cases"]:
            case_id = excluded_case.get("id")
            prompt = excluded_case.get("prompt")
            locations = excluded_case.get("locations")
            if (
                not isinstance(case_id, str)
                or not isinstance(prompt, str)
                or not isinstance(locations, list)
            ):
                raise AgentHoldoutDataError("holdout exclusion case is invalid")
            excluded_ids.add(case_id)
            excluded_prompts.add(prompt)
            for location in locations:
                if not isinstance(location, dict):
                    raise AgentHoldoutDataError("holdout exclusion location is invalid")
                line_min = location.get("line_min")
                line_max = location.get("line_max")
                if line_min is None and line_max is None:
                    continue
                if not isinstance(line_min, int) or not isinstance(line_max, int):
                    raise AgentHoldoutDataError("holdout exclusion location is invalid")
                excluded_locations.append(
                    (location["repo"], location["path"], line_min, line_max)
                )

    for case in cases:
        if case.id in excluded_ids:
            raise AgentHoldoutDataError("holdout id overlaps the exclusion set")
        if case.question in excluded_prompts:
            raise AgentHoldoutDataError("holdout prompt overlaps the exclusion set")
        if not isinstance(case.expected, agent_eval.AnswerableExpected):
            continue
        for location in case.expected.locations:
            for repo, path, line_min, line_max in excluded_locations:
                overlaps = (
                    location.repo == repo
                    and location.path == path
                    and location.line_min <= line_max
                    and line_min <= location.line_max
                )
                if overlaps:
                    raise AgentHoldoutDataError(
                        "holdout gold location overlaps the exclusion set"
                    )


def validate_frozen_holdout_dataset(
    cases_path: Path = DEFAULT_HOLDOUT_CASES_PATH,
    exclusion_path: Path = DEFAULT_EXCLUSION_SET_PATH,
) -> tuple[tuple[agent_eval.AgentEvalCase, ...], str, dict[str, Any]]:
    cases = agent_eval.load_agent_cases(cases_path)
    digest = agent_eval.dataset_sha256(cases_path)
    if digest != FROZEN_HOLDOUT_DATASET_SHA256:
        raise AgentHoldoutDataError("agent holdout data hash does not match freeze")
    exclusion_set = _load_exclusion_set(exclusion_path)
    validate_holdout_disjointness(cases, exclusion_set)
    return cases, digest, exclusion_set


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or run the independently frozen Click holdout once."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evaluation/agent_holdout_cases_2026-08-19.jsonl"),
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--summary-out", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-proxy-base-url", action="store_true")
    return parser.parse_args(argv)


def _print_stable_error(error_type: str) -> None:
    print(
        json.dumps(
            {"status": "error", "error_type": error_type},
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=agent_eval.sys.stderr,
    )


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    cases_path = agent_eval.resolve_project_path(args.cases)
    try:
        cases, digest, exclusion_set = validate_frozen_holdout_dataset(cases_path)
    except (OSError, agent_eval.AgentEvalDataError):
        _print_stable_error("invalid_or_unfrozen_holdout_dataset")
        return 2

    if args.validate_only:
        print(
            json.dumps(
                {
                    "answerable_cases": 8,
                    "case_count": len(cases),
                    "click_revision": agent_eval.PINNED_CLICK_REVISION,
                    "dataset_sha256": digest,
                    "exclusion_counts": EXPECTED_EXCLUSION_COUNTS,
                    "exclusion_set_sha256": FROZEN_EXCLUSION_SET_SHA256,
                    "insufficient_evidence_cases": 2,
                    "status": "valid",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    if cases_path != DEFAULT_HOLDOUT_CASES_PATH.resolve():
        _print_stable_error("real_eval_requires_default_frozen_holdout_dataset")
        return 2

    try:
        api_key = agent_eval._required_environment("OPENAI_API_KEY")
        base_url = agent_eval._required_environment("OPENAI_BASE_URL")
        model_name = agent_eval._required_environment("OPENAI_MODEL")
        declared_index_revision = agent_eval._required_environment(
            "ZOEKT_INDEX_REVISION"
        )
        if args.no_proxy_base_url:
            agent_eval.configure_no_proxy_for_base_url(base_url)

        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)

        from src.config import get_repository_root
        from src.server import get_file_context, search_code

        click_repository = get_repository_root("click")
        preflight = await agent_eval.preflight_environment(
            repository=click_repository,
            declared_index_revision=declared_index_revision,
            search_code=search_code,
        )
    except agent_eval.EnvironmentPreflightError as exc:
        _print_stable_error(str(exc))
        return 3
    except Exception:
        _print_stable_error("environment_preflight_failed")
        return 3

    client = agent_eval.OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=60.0,
    )
    model = agent_eval.OpenAIModel(client=client, model=model_name)
    started_at = datetime.now(timezone.utc)
    records: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        record = await agent_eval.evaluate_agent_case(
            case,
            model=model,
            model_name=model_name,
            search_code=search_code,
            get_file_context=get_file_context,
        )
        records.append(record)
        final = record["final_answer"]
        reason = final["termination_reason"] if final is not None else "missing"
        print(
            f"[{index}/{len(cases)}] {case.id} "
            f"success={int(record['success'])} terminal={reason} "
            f"tools={record['tool_attempts']['count']}"
        )

    finished_at = datetime.now(timezone.utc)
    report = agent_eval.build_report(
        cases_path=cases_path,
        dataset_digest=digest,
        cases=cases,
        records=records,
        preflight=preflight,
        model_name=model_name,
        started_at=started_at,
        finished_at=finished_at,
    )
    report["evaluation"] = "frozen_click_agent_holdout_eval"
    report["dataset"]["exclusion_set"] = {
        "path": agent_eval.display_path(DEFAULT_EXCLUSION_SET_PATH),
        "sha256": FROZEN_EXCLUSION_SET_SHA256,
        "case_counts": {
            source["kind"]: source["case_count"] for source in exclusion_set["sources"]
        },
    }
    try:
        repository_path = str(click_repository.resolve())
        redaction = agent_eval.assert_report_is_sanitized(
            report,
            sensitive_values=(api_key, base_url, repository_path),
        )
        report["redaction_evidence"] = redaction
        agent_eval.assert_report_is_sanitized(
            report,
            sensitive_values=(api_key, base_url, repository_path),
        )
    except RuntimeError:
        _print_stable_error("report_sanitization_failed")
        return 4

    date_string = datetime.now().strftime("%Y-%m-%d")
    out_path = agent_eval.resolve_project_path(
        args.out
        or Path(f"evaluation/reports/agent-holdout-eval-{date_string}.json")
    )
    summary_path = agent_eval.resolve_project_path(
        args.summary_out
        or Path(f"evaluation/reports/agent-holdout-eval-{date_string}.md")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rendered = agent_eval.render_markdown_report(report).replace(
        "# Frozen Click Agent Eval — 2026-08-16",
        f"# Frozen Click Agent Holdout Eval — {date_string}",
        1,
    )
    summary_path.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "json_report": agent_eval.display_path(out_path),
                "markdown_report": agent_eval.display_path(summary_path),
                "m3_exit_criteria_met": report["verdict"]["m3_exit_criteria_met"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        _print_stable_error("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
