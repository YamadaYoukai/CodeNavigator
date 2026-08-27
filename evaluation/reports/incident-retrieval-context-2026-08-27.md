# Incident Retrieval Context Mapping Evidence — 2026-08-27

## Result

**PASS.** The repository now has a deterministic, fully offline adapter from a
source-grounded `IncidentExtractionResult` to ordered typed
`IncidentRetrievalTask` candidates and a `ContextState` accepted by the
existing `ContextBuilder`. This result proves construction and provenance
behavior only; it does not prove retrieval or Incident Copilot quality.

The verified parent was `a01feb8606581f1f7a08982cb0b34aef42667e1f`
(`feat: add structured incident extraction boundary`). Before implementation,
the main worktree was clean, `main` was seven commits ahead of `origin/main`,
and no Incident context adapter or equivalent task model existed.

## Baseline and red test

- Existing Incident extraction focus: `35 passed`.
- The parent evidence recorded a full-suite baseline of `284 passed`.
- The new focused test was run before production implementation and failed
  during collection because `IncidentContextValidationError` and the new
  mapping API did not yet exist.
- No model, tool, network service, local repository index, or company data was
  used to create the red or green result.

## Frozen mapping contract

- The mapper accepts the original `IncidentInput` together with the purported
  `IncidentExtractionResult`. It deep-copies both, downgrades the result to an
  untrusted candidate, and reuses the existing exact source-association gate
  before constructing tasks or state. A directly constructed result cannot
  bypass fabricated-value, unknown-ID, case, Unicode, or JSON-type checks.
- Present values produce tasks in this order: method, exception class, every
  configuration key in extraction order, error text, then service name.
  Missing values produce no placeholder. Tasks are neither merged nor
  truncated to the remaining tool-call budget.
- Every task retains the exact untrimmed value and ordered source IDs. Its
  shared `SearchCodeArguments` uses the same query, `literal=true`, null
  language/path, and the router's existing default limit.
- Repository selection is explicit. A non-null selection must exactly match a
  supplied canonical repository name. Alias, case, URL, basename, package, and
  service-name inference are rejected; a null selection remains null even when
  a service name equals a canonical hint.
- Each original source becomes an ordered fact with source label
  `incident:<source_type>:<source_id>` and unchanged caller-redacted content.
  `current_task` is stable JSON containing the complete ordered task payloads.
- System instruction, repository hints, evidence budget, and remaining tool
  calls are caller-owned values. Evidence trimming remains the existing
  `ContextBuilder` responsibility and does not change the mapper's task list or
  source facts.

## Public offline fixture coverage

The existing synthetic public fixture was reused unchanged:
`evaluation/fixtures/incident-extraction-public-cases-2026-08-26.json`.

SHA-256:
`2a39e8d7afed1fe37e1168e0987653fb94ab4b919839545bd472ebbb834b2108`

Coverage includes:

- five ordered Java tasks with exact queries and source relationships;
- four source facts surviving through `ContextBuilder` into `ModelInput`;
- ordered multiple configuration keys with absent method/exception omitted;
- Chinese task values and source text surviving JSON round trips;
- forged, unknown-source, case-drift, Unicode-drift, and scalar-type failures;
- exact canonical repository acceptance and alias/case/unknown rejection;
- untrimmed query preservation, deterministic bytes, detached inputs and
  outputs, and evidence-budget ownership;
- patched model/tool boundaries recording zero calls during mapping and model
  input construction.

## Verification

All Python commands disabled bytecode and pytest cache writes where applicable.

| Check | Result |
| --- | --- |
| Incident context + extraction focus | `52 passed` |
| full `pytest` | `301 passed` |
| retrieval `--validate-only` | `18` cases valid |
| Agent `--validate-only` | `10` cases valid; frozen hash unchanged |
| holdout `--validate-only` | `10` cases valid; frozen data and exclusion hashes unchanged |
| public package import | `IncidentRetrievalTask` and `ContextState` import successfully |
| whitespace/error check | `git diff --check` passes |

## Content-safety scan

The scan covers the new mapper, tests, design contract, and this report. It
checks for local home-directory paths, email-address shapes, internal-domain
fragments, common cloud/API credential prefixes, credential assignment forms,
and private-key headers. It returned no matches.

This is a pattern-based content scan only. The caller remains responsible for
redacting Incident input before constructing `IncidentInput`.

## Honest boundary and next step

No real extractor, model, Zoekt search, MCP client, `ToolRouter.execute`, Agent
Loop, checkpoint, root-cause ranking, recommendation, retry, or failure
injection was added or invoked. The tasks are candidates, not executed or
quality-scored retrievals.

This evidence leaves M4 at `10%` and total roadmap progress at `56.5%`. A later
increment may freeze one real read-only `search_code` execution gate for a
public Incident fixture; it is deliberately outside this change.
