# Frozen Range-Citation Failures and Next Hypothesis — 2026-08-16

## Frozen evidence boundary

This note freezes the six answerable cases from Recovery Retry 01 that were
classified as `model_judgment_failure` and contained at least one submitted
citation whose stable parse error was `invalid_positive_line`.

- Source report: [`agent-eval-2026-08-16-retry-01.json`](agent-eval-2026-08-16-retry-01.json)
- Source report SHA-256: `b4fa733f3133ad8377799e0db5ac986c8f08342bae8d13873902b2494054671c`
- Frozen dataset SHA-256: `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129`
- Machine-readable freeze: [`agent-eval-range-citation-failures-2026-08-16.json`](../fixtures/agent-eval-range-citation-failures-2026-08-16.json)
- Selection result: 6 cases, 15 submitted citations, 14 range-shaped parse
  failures, 1 exact current-Trace Tool fact, and 0 gold matches.

The fixture copies each complete `submitted_final_decision`, including the
model's answer, evidence, uncertainties, and next queries. Its reviewed
`expected_single_line_citations` are canonical single-line facts already
authorized by that case's captured successful Tool trace. They are reference
outputs for the next experiment, not edits to the frozen dataset or gold.

## Case register

Every row has the same stable raw classification and terminal:
`model_judgment_failure` / `insufficient_evidence`. The more specific failure
mechanism is `range_citation_not_exact_tool_fact`; the runtime correctly
failed closed because a range string is not a complete fact in the exact
current-task provenance set.

| Case | Model-submitted evidence | Reviewed single-line reference evidence |
| --- | --- | --- |
| `agent-click-open-file-001` | `utils.py:370-416`; `utils.py:378-403`; `utils.py:113-177`; `utils.py:184-204` | `utils.py:370`, `382`, `398`, `401`, `406`, `411`, `413`, `142`, `169`, `188` |
| `agent-click-format-filename-001` | `utils.py:159`; `utils.py:419-458` | `utils.py:159`, `419`, `423`, `446`, `451`, `454` |
| `agent-click-default-map-001` | `core.py:761-771`; `core.py:735-739` | `core.py:735`, `751`, `755`, `761`, `766`, `768`, `771` |
| `agent-click-extra-args-001` | `core.py:1281-1288` | `core.py:1281`, `1282`, `1284`, `1285`, `1286` |
| `agent-click-command-normalize-001` | `core.py:1946-1970`; `core.py:1927-1938`; `core.py:427-432` | `core.py:1928`, `1946`, `1949`, `1952`, `1956`, `1957`, `1958`, `1966`, `1970`, `427`, `428` |
| `agent-click-choice-message-001` | `types.py:358-366`; `types.py:302-318`; `types.py:320-338` | `types.py:302`, `312`, `320`, `330`, `332`, `335`, `358`, `364`, `365` |

All abbreviated entries above use repository `click` and path prefix
`src/click/`; the fixture stores every expected citation as separate `repo`,
`path`, and positive integer `line` fields.

## Next falsifiable hypothesis: structured citation decisions

**Hypothesis `H-SC-01`:** If the model-facing final-answer decision changes
only from free-text evidence strings to strict citation objects of the form
`{"repo":"click","path":"src/click/utils.py","line":411}`, then at least
5 of the 6 frozen range-failure cases will terminate `completed` in one run,
without relaxing current-task provenance validation.

The independent variable is the decision contract:

```json
{
  "decision_type": "final_answer",
  "answer": "non-empty answer",
  "evidence": [
    {"repo": "click", "path": "src/click/utils.py", "line": 411}
  ],
  "uncertainties": [],
  "next_queries": []
}
```

The implementation under test must validate `repo`, `path`, and `line` as
separate strict fields before producing the canonical internal
`repo/path:line` value. `line` must be a positive integer. The runtime must
continue to require exact membership in the current task's correlated,
successful Tool facts; it must not accept `start`, `end`, a range string,
numeric coercion, path repair, fuzzy matching, clamping, or citations from a
different task.

### Controls

- Same 10-case dataset, dataset hash, Click revision, case order, gold ranges,
  model configuration, retrieval tools, Tool descriptions, six-Tool budget,
  and one execution per case.
- Keep the system instruction semantically unchanged except for describing the
  structured evidence fields, and change only the final-decision protocol and
  parser required to expose that contract.
- No retry, best-of selection, result-driven Prompt edit, gold edit, or
  post-processing repair.

### Pre-registered decision rule

`H-SC-01` is supported only if all of the following hold in the single full
run:

1. At least 5/6 frozen range-failure cases terminate `completed`, each with at
   least one gold-matching citation and every submitted citation backed by an
   exact current-task successful Tool fact.
2. No case fails because a range-shaped citation reached the runtime, and no
   more than one of the six cases substitutes `invalid_model_output` for the
   old citation failure.
3. Both frozen negative cases still terminate `insufficient_evidence` with
   empty evidence, all 10 traces remain complete, and no case exceeds six Tool
   attempts.

The hypothesis is rejected if any safety invariant is weakened, if either
negative case becomes an unsupported answer, or if fewer than 5 of the 6
target cases complete. A result that merely converts the old failures into
schema/parser failures also rejects the hypothesis. This experiment has been
specified but has not yet been run.
