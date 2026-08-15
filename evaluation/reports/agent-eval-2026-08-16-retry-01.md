# Frozen Click Agent Eval — 2026-08-16 Recovery Retry 01

## Retry context

This is a user-authorized recovery retry after the original round ended 10/10 with `model_execution_error`. The original [JSON](agent-eval-2026-08-16.json) and [Markdown](agent-eval-2026-08-16.md) remain unchanged as the first-round evidence. Before this retry, the configured model completed a redaction-checked smoke call in 7201 ms. The frozen dataset, SHA-256, gold ranges, system instruction, Tool descriptions, retrieval behavior, and acceptance rules were unchanged. Each case ran once in this round, no best-of selection was performed, and no third round was run.

## Verdict

M3 exit criteria are **not met** for this evaluation.

The dataset was frozen before model calls at SHA-256 `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129` with 10 cases (8 answerable, 2 expected information-insufficient) on Click revision `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`. Every case was executed once in fixed file order; no best-of selection or result-driven Prompt, retrieval, or gold changes were made.

## Metrics

| Metric | Result |
| --- | ---: |
| Task success | 3/10 (30.00%) |
| Citation validity | 6/20 (30.00%) |
| Trace integrity | 10/10 (100.00%) |
| Tool attempts | avg 4.40, max 6 |

Termination reasons: `{"completed": 1, "insufficient_evidence": 8, "tool_error": 1}`.

Tool-attempt distribution: `{"2": 2, "3": 1, "4": 2, "5": 1, "6": 4}`.

Observed latency (milliseconds):

- Model calls: `{"average": 8990.283018867925, "count": 53, "maximum": 40613, "minimum": 3928, "total": 476485}`
- Tools: `{"average": 9.886363636363637, "count": 44, "maximum": 55, "minimum": 1, "total": 435}`
- Tasks: `{"average": 47713.4, "count": 10, "maximum": 68364, "minimum": 23355, "total": 477134}`

## Failure classification

- `model_judgment_failure`: 6 — `agent-click-open-file-001`, `agent-click-format-filename-001`, `agent-click-default-map-001`, `agent-click-extra-args-001`, `agent-click-command-normalize-001`, `agent-click-choice-message-001`
- `tool_or_service_unavailable`: 1 — `agent-click-echo-001`

## Per-case results

| Case | Expected | Terminal | Tools | Trace | Success | Failure |
| --- | --- | --- | ---: | --- | --- | --- |
| `agent-click-echo-001` | answerable | `tool_error` | 4 | pass | fail | `tool_or_service_unavailable` |
| `agent-click-open-file-001` | answerable | `insufficient_evidence` | 6 | pass | fail | `model_judgment_failure` |
| `agent-click-format-filename-001` | answerable | `insufficient_evidence` | 4 | pass | fail | `model_judgment_failure` |
| `agent-click-progressbar-001` | answerable | `completed` | 6 | pass | pass | `—` |
| `agent-click-default-map-001` | answerable | `insufficient_evidence` | 2 | pass | fail | `model_judgment_failure` |
| `agent-click-extra-args-001` | answerable | `insufficient_evidence` | 2 | pass | fail | `model_judgment_failure` |
| `agent-click-command-normalize-001` | answerable | `insufficient_evidence` | 3 | pass | fail | `model_judgment_failure` |
| `agent-click-choice-message-001` | answerable | `insufficient_evidence` | 5 | pass | fail | `model_judgment_failure` |
| `agent-click-no-symbol-001` | insufficient_evidence | `insufficient_evidence` | 6 | pass | pass | `—` |
| `agent-click-no-origin-registry-001` | insufficient_evidence | `insufficient_evidence` | 6 | pass | pass | `—` |

## Environment and honest boundary

Checkout and declared index revision both matched `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`; all 3 pinned content probes passed, `zoekt.name` was `click`, and the existing 18-case retrieval data contract validated.

The Zoekt server does not expose its build commit or an embedded source commit. Index consistency is therefore supported by the operator-declared pinned revision plus positive and negative content probes, not by a server-native revision field. Results apply only to this Click 8.4.1, Python, single-repository setup and are not a production or cross-language quality claim.

The companion JSON preserves every sanitized case trace, submitted final decision, Tool sequence, citation provenance check, gold match, invariant result, timing, and raw stable failure classification needed to recompute these metrics.

## Retry diagnosis and conclusion

- The model service recovered: 53 model calls completed far enough for the Agent loop to make Tool and final-answer decisions.
- Three cases passed: the `progressbar` answer and both expected negative refusals.
- Six answerable cases submitted one or more range-shaped references such as `repo/path:start-end`. The frozen citation contract accepts only complete single-line `repo/path:line` values, so those answers correctly failed closed as `insufficient_evidence` and remain `model_judgment_failure` cases.
- The `echo` case ended after four searches with a stable `tool_execution_error`; no raw backend exception was retained.
- All 10 traces were complete and every task stayed within the six-Tool limit, but citation validity was only 6/20 (30%). M3 therefore remains incomplete.
