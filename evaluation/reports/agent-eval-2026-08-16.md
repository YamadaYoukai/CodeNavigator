# Frozen Click Agent Eval — 2026-08-16

## Verdict

M3 exit criteria are **not met** for this evaluation.

The dataset was frozen before model calls at SHA-256 `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129` with 10 cases (8 answerable, 2 expected information-insufficient) on Click revision `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`. Every case was executed once in fixed file order; no best-of selection or result-driven Prompt, retrieval, or gold changes were made.

## Metrics

| Metric | Result |
| --- | ---: |
| Task success | 0/10 (0.00%) |
| Citation validity | 0/0 (n/a) |
| Trace integrity | 10/10 (100.00%) |
| Tool attempts | avg 0.00, max 0 |

Termination reasons: `{"model_execution_error": 10}`.

Tool-attempt distribution: `{"0": 10}`.

Observed latency (milliseconds):

- Model calls: `{"average": 5041.8, "count": 10, "maximum": 5335, "minimum": 5007, "total": 50418}`
- Tools: `{"average": null, "count": 0, "maximum": null, "minimum": null, "total": null}`
- Tasks: `{"average": 5042.8, "count": 10, "maximum": 5336, "minimum": 5008, "total": 50428}`

## Failure classification

- `tool_or_service_unavailable`: 10 — `agent-click-echo-001`, `agent-click-open-file-001`, `agent-click-format-filename-001`, `agent-click-progressbar-001`, `agent-click-default-map-001`, `agent-click-extra-args-001`, `agent-click-command-normalize-001`, `agent-click-choice-message-001`, `agent-click-no-symbol-001`, `agent-click-no-origin-registry-001`

## Per-case results

| Case | Expected | Terminal | Tools | Trace | Success | Failure |
| --- | --- | --- | ---: | --- | --- | --- |
| `agent-click-echo-001` | answerable | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-open-file-001` | answerable | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-format-filename-001` | answerable | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-progressbar-001` | answerable | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-default-map-001` | answerable | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-extra-args-001` | answerable | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-command-normalize-001` | answerable | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-choice-message-001` | answerable | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-no-symbol-001` | insufficient_evidence | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-no-origin-registry-001` | insufficient_evidence | `model_execution_error` | 0 | pass | fail | `tool_or_service_unavailable` |

## Environment and honest boundary

Checkout and declared index revision both matched `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`; all 3 pinned content probes passed, `zoekt.name` was `click`, and the existing 18-case retrieval data contract validated.

The Zoekt server does not expose its build commit or an embedded source commit. Index consistency is therefore supported by the operator-declared pinned revision plus positive and negative content probes, not by a server-native revision field. Results apply only to this Click 8.4.1, Python, single-repository setup and are not a production or cross-language quality claim.

The companion JSON preserves every sanitized case trace, submitted final decision, Tool sequence, citation provenance check, gold match, invariant result, timing, and raw stable failure classification needed to recompute these metrics.

## Execution closure

- Actual execution date: `2026-08-16` (Asia/Shanghai).
- Gate A implementation commit: `7be2de9` (`feat: validate final answer evidence provenance`).
- Frozen data, Runner, raw JSON, and initial summary commit: `c280fcc` (`feat: add frozen agent evaluation evidence`).
- Focused evidence/Loop regression: `25 passed`.
- Focused Agent Eval Runner regression: `14 passed`.
- Final full regression: `163 passed`; package imports and `git diff --check` also passed.
- Offline data gates: 18 retrieval cases validated; 10 Agent cases validated at SHA-256 `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129`.
- Real retrieval preflight on the newly built pinned index: Hit@1 `16/16`, Hit@5 `16/16`, closed loop `16/16`, and expected no-match `2/2`.
- Dedicated Click index SHA-256: `4808aa83857204cb80cb934e7c8c37b215194a2bb92ecd8753298a7d3c263000`; it was built from the clean pinned checkout before the Agent run.
- Raw detail report: `evaluation/reports/agent-eval-2026-08-16.json`; this Markdown file is the summary evidence.
- Unfinished gate: the configured model service returned `model_execution_error` on every first call, leaving citation validity at `0/0 (n/a)` and task success at `0/10`. The frozen run was not repeated. M3 remains incomplete.
