# Frozen Click Agent Holdout Eval — 2026-08-22

## Verdict

M3 exit criteria are **met** for this frozen single-repository evaluation.

The dataset was frozen before model calls at SHA-256 `e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396` with 10 cases (8 answerable, 2 expected information-insufficient) on Click revision `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`. Every case was executed once in fixed file order; no best-of selection or result-driven Prompt, retrieval, or gold changes were made.

## Metrics

| Metric | Result |
| --- | ---: |
| Task success | 9/10 (90.00%) |
| Citation validity | 45/45 (100.00%) |
| Trace integrity | 10/10 (100.00%) |
| Tool attempts | avg 4.80, max 6 |

Termination reasons: `{"completed": 7, "insufficient_evidence": 2, "tool_error": 1}`.

Tool-attempt distribution: `{"1": 1, "2": 1, "3": 1, "6": 7}`.

Observed latency (milliseconds):

- Model calls: `{"average": 5837.964912280701, "count": 57, "maximum": 19485, "minimum": 3444, "total": 332764}`
- Tools: `{"average": 9.25, "count": 48, "maximum": 87, "minimum": 1, "total": 444}`
- Tasks: `{"average": 33337.8, "count": 10, "maximum": 49551, "minimum": 5082, "total": 333378}`

## Failure classification

- `tool_or_service_unavailable`: 1 — `holdout-click-command-main-001`

## Per-case results

| Case | Expected | Terminal | Tools | Trace | Success | Failure |
| --- | --- | --- | ---: | --- | --- | --- |
| `holdout-click-unpack-args-001` | answerable | `completed` | 6 | pass | pass | `—` |
| `holdout-click-pass-decorator-001` | answerable | `completed` | 3 | pass | pass | `—` |
| `holdout-click-datetime-convert-001` | answerable | `completed` | 6 | pass | pass | `—` |
| `holdout-click-bad-parameter-001` | answerable | `completed` | 6 | pass | pass | `—` |
| `holdout-click-write-dl-001` | answerable | `completed` | 2 | pass | pass | `—` |
| `holdout-click-runner-isolation-001` | answerable | `completed` | 6 | pass | pass | `—` |
| `holdout-click-editor-selection-001` | answerable | `completed` | 6 | pass | pass | `—` |
| `holdout-click-command-main-001` | answerable | `tool_error` | 1 | pass | fail | `tool_or_service_unavailable` |
| `holdout-click-no-session-store-001` | insufficient_evidence | `insufficient_evidence` | 6 | pass | pass | `—` |
| `holdout-click-no-sandbox-policy-001` | insufficient_evidence | `insufficient_evidence` | 6 | pass | pass | `—` |

## Environment and honest boundary

Checkout and declared index revision both matched `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`; all 3 pinned content probes passed, `zoekt.name` was `click`, and the existing 18-case retrieval data contract validated.

The Zoekt server does not expose its build commit or an embedded source commit. Index consistency is therefore supported by the operator-declared pinned revision plus positive and negative content probes, not by a server-native revision field. Results apply only to this Click 8.4.1, Python, single-repository setup and are not a production or cross-language quality claim.

The companion JSON preserves every sanitized case trace, submitted final decision, Tool sequence, citation provenance check, gold match, invariant result, timing, and raw stable failure classification needed to recompute these metrics.
