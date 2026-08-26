# Structured Incident Extraction Boundary Evidence — 2026-08-26～2026-08-27

## Result

**PASS.** The repository now has a strict, deterministic, fully offline
boundary for caller-redacted incident source lines and five source-grounded
output categories. The result is limited to contract and provenance behavior;
it does not demonstrate real extraction quality.

Implementation began on 2026-08-26 and final verification completed on
2026-08-27 (Asia/Shanghai); the dated fixture and report paths remain the
frozen names selected by the execution plan.

The verified parent was `058886019fe1172c802a2c37415e51b8ed793204`
(`fix: reject checkpoint scalar type drift`). Before implementation, the main
worktree was clean, `main` was six commits ahead of `origin/main`, and no
Incident input or extraction module existed.

## Baseline and red test

- Existing checkpoint/resume focus: `58 passed`.
- Existing full suite: `249 passed`.
- Frozen retrieval data: `18` cases valid.
- Frozen Agent data: `10` cases valid, dataset SHA-256
  `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129`.
- Frozen holdout data: `10` cases (`8` answerable, `2` insufficient evidence)
  valid, dataset SHA-256
  `e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396`.
- The new focused test was run before implementation and failed during
  collection because `FakeIncidentFieldExtractor` and the other new public
  types did not exist. No production implementation was present for that red
  run.

## Frozen contract

- `IncidentInput` contains one or more ordered `IncidentSource` lines. Every
  line has a unique, whitespace-free `source_id`, an explicit
  `description`/`log`/`stack_trace` type, and non-whitespace text. Multiline
  sources, coercion, extra fields, unknown source types, and Python container
  drift are rejected. Caller text, including indentation and Unicode, is never
  trimmed or normalized.
- A candidate and a validated result contain exactly `exception_class`,
  `method`, `error_text`, `service_name`, and `configuration_keys`. Missing
  scalar fields are explicit `null`; missing configuration keys are an empty
  ordered array. Every top-level field is required.
- Every non-empty value has at least one unique source ID. Every cited ID must
  exist, and the value must occur verbatim and case-sensitively in every cited
  source line. Unknown IDs, fabricated text, case-only matches, fuzzy matches,
  and partial citation support fail closed.
- Strict text fields reject boolean, integer, float, and string coercion.
  Candidate-to-result comparison additionally walks recursive JSON containers,
  scalar runtime types, and values, preserving `true`, `1`, and `1.0` as
  distinct identities.
- Inputs, candidate scripts, Fake observations, and trusted results are copied
  deeply at their boundaries. Stable JSON uses sorted keys, fixed separators,
  unescaped Unicode, and no time, randomness, host, or process state.

## Public offline fixtures

Fixture:
`evaluation/fixtures/incident-extraction-public-cases-2026-08-26.json`

SHA-256:
`2a39e8d7afed1fe37e1168e0987653fb94ab4b919839545bd472ebbb834b2108`

| Case | Positive observations | Explicitly missing |
| --- | --- | --- |
| Java exception stack | exception class, method, error text, service name, one configuration key | none |
| Multiple missing configuration keys | error text, service name, two ordered configuration keys | exception class, method |
| Unicode and missing fields | Chinese error text and service name | exception class, method, configuration keys |

All values and identifiers are synthetic. The Fake only replays the fixture's
candidate; the positive cases do not claim that a model inferred these fields.

## Negative and determinism matrix

The focused suite rejects:

- empty input, duplicate IDs, unknown source type, multiline or whitespace-only
  sources, additional fields, and wrong scalar/container types;
- omitted or additional output categories, empty observations or source lists,
  duplicate source references, duplicate configuration keys, and configuration
  container drift;
- boolean, integer, and equal-valued float substitution at strict string
  boundaries;
- fabricated field text, unknown source IDs, case-insensitive-only support, and
  a value that is supported by only a subset of its cited lines;
- a Fake script containing the wrong type, script exhaustion, and an extractor
  returning an untyped mapping.

Repeated input/candidate validation produces identical JSON bytes. Tests also
mutate detached payloads and repeated Fake properties and confirm that the
input, script, recorded snapshot, and trusted result remain unchanged.

## Verification

All commands ran with bytecode and pytest cache writes disabled where
applicable.

| Check | Result |
| --- | --- |
| `pytest test/test_incident_extraction.py` | `35 passed` |
| full `pytest` | `284 passed` |
| retrieval `--validate-only` | `18` cases valid |
| Agent `--validate-only` | `10` cases valid; frozen hash unchanged |
| holdout `--validate-only` | `10` cases valid; frozen data and exclusion hashes unchanged |
| public package import | `IncidentInput` and `IncidentFieldExtractor` import successfully |
| Python compilation | new module compiles successfully |
| whitespace/error check | `git diff --check` passes |

## Content-safety scan

The scan covers the new public fixture, implementation, test, design section,
and this report. Rules look for email-address shapes, local home-directory
prefixes, known corporate-domain fragments, internal hostname suffixes, common
cloud/API credential prefixes, credential assignment forms, and private-key
headers. The scan returned no matches.

This is a content scan only. It does not claim to inspect Git history, commit
author metadata, untracked files outside this change, or arbitrary secrets that
do not match the stated rules. The caller remains responsible for redaction
before creating `IncidentInput`.

## Honest boundary and next step

No real model, OpenAI-compatible endpoint, Zoekt instance, MCP client, Context,
Tool, Agent Loop, checkpoint, cause hypothesis, retry, or failure injection was
added or invoked. Existing Agent and checkpoint contracts are unchanged.

This evidence supports only: “M4's structured Incident input and extraction
boundary is complete.” With only a scripted Fake and no extraction/retrieval
loop, M4 remains `10%` and total roadmap progress remains `56.5%`. The next
technical increment is a deterministic conversion from validated Incident
fields to a retrieval task/`ContextState`, demonstrated with one public offline
fixture.
