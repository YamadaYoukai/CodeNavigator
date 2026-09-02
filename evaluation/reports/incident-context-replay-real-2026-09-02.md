# Successful Real Incident Context-Suffix Replay — 2026-09-02

## Result

**PASSED (branch A).** The immutable successful 2026-08-30 search artifact was
strictly linked to the original public Incident fixture. After all offline
tests and a zero-Tool local preflight passed, exactly one real
`src.server.get_file_context` call read the frozen Top-1 Click location.

This run did not call `search_code`, a model, an Agent Loop, MCP transport, or
the network. There was no retry, alternate line, widened context window, or
change to the fixture, upstream artifact, Top-1, revision, or gold.

## Frozen source chain

- fixture SHA-256:
  `2634a72fca1c8702e0250b8948241ae0d0a6dceaf18e8f6b56039a5734944fe0`;
- successful search artifact SHA-256:
  `41607f1a163440d3b517bb05e637f15dc8c1924b11c26323b9f91f57a5500acf`;
- upstream search Tool-result SHA-256:
  `af3be0e24a5c84950215d62178da6a0f6220e2bbaebc5f85f0339fbe69bb858e`;
- Click revision: `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`;
- validated Top-1: `click/src/click/core.py:1284`;
- context arguments SHA-256:
  `6a3666e7f4b509cdeb3d06b0bdd77bf9fe59ba412b58b55a77c45c86ffaa9f2c`.

The runner rejects byte-hash drift before parsing. With valid frozen bytes, a
strict schema and explicit invariant checks reject missing or extra fields,
wrong status/counts/budget/Trace/source checks, non-Top-1 evidence, and JSON
scalar substitutions such as `true` or `1.0` for an integer rank or line.

## Zero-Tool preflight

| Check | Result |
| --- | --- |
| fixture and upstream artifact identity | passed |
| Click checkout revision | passed |
| canonical `zoekt.name=click` | passed |
| target file and frozen target line | passed |
| frozen `lines_before=20`, `lines_after=20` | passed |
| expected bounds `1264..1304` | passed |
| expected context-content SHA-256 | passed |

Preflight read only the pinned local checkout. It did not call either Tool or
send a network request.

## Sole real context attempt

| Field | Observed result |
| --- | --- |
| status | `success` |
| `search_code` | `0` |
| `get_file_context` | `1` |
| model | `0` |
| Agent Loop | `0` |
| suffix budget | `1 -> 0` |
| suffix Trace | `tool_call -> tool_result` |
| context range | `click/src/click/core.py:1264..1304` |
| target line | `1284` |
| file total lines | `3542` |
| context content SHA-256 | `33955d1d88da4656c038ed3e3b4e5a24201b57477d95bf9ab15cbc466bf728d4` |
| context Tool-result SHA-256 | `d6a654cc31e1efd119f206da9d949332aa8cc206f6d8449645d506dfdc337818` |

The fresh context fact, validated upstream search fact, and original Incident
source fact are each present in the next `ContextState` and immediately next
`ModelInput`.

## Artifact and offline verification

The allowlisted JSON artifact is
`evaluation/reports/incident-context-replay-real-2026-09-02.json`. Its
SHA-256 is:

`dae39f4618cb15d0f353db4d5c5bc131b524be2aa226970f1908b4f161646545`

The saved bytes passed strict schema parsing and invariant recomputation.
Before the real call, the Incident context/search/context-suffix focused suite
reported `72 passed`; the complete suite reported `356 passed`. The frozen
retrieval, Agent, and holdout datasets remained valid at `18/10/10` cases.
After adding the artifact-specific immutable-hash regression, the focused
suite reported `73 passed` and the complete suite reported `357 passed`.
Package imports and `git diff --check` also passed. Final post-commit checks
are recorded in the commit handoff.

The JSON/Markdown evidence contains only stable statuses, counts, relative
source coordinates, and content hashes. It excludes raw Incident/query text,
returned source content, local repository roots, endpoints, credentials, and
backend exception details.

## Honest boundary

Together with the immutable 2026-08-30 search artifact, this proves a
deterministic cross-artifact `search_code -> get_file_context` chain for one
fixed public Click case. It does not prove that both Tools were model-selected
or executed in one Trace, MCP Client transport, root-cause correctness,
retry/recovery behavior, multiple Incident coverage, or a complete Incident
Copilot.
