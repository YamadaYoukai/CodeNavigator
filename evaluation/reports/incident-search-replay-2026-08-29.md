# Public Incident Search Replay Evidence — 2026-08-29

> This file preserves the first branch-C preflight block. The user later
> supplied the missing endpoint configuration and authorized the first real
> attempt. That attempt is recorded separately in
> `incident-search-replay-real-2026-08-29.md` and failed closed as branch B;
> it does not change the facts recorded below about the earlier zero-call stop.

## Result

**BLOCKED (branch C).** The frozen public Incident fixture and the offline
single-step runner passed their gates, but the real environment preflight
failed closed because explicit Zoekt URL configuration was missing. No real
`search_code`, `get_file_context`, model, Agent Loop, or network probe was
called. This is not a successful Incident retrieval chain.

M4 remains `10%`, total roadmap progress remains `56.5%`, and no technical
commit was created. The same fixture must be retained for the first real
attempt after the environment is restored.

## Independent baseline acceptance

- Start HEAD: `30fab73f1ec0ad631deb4147a5e3c0d1ab982231`.
- Start worktree: clean; `main` was eight commits ahead of `origin/main`.
- `30fab73` changed only the Incident mapper, its typed task/export, focused
  tests, design contract, and public evidence.
- The independent Incident extraction/context run passed `52` tests before
  the new fixture or runner was added.

## Frozen public fixture

The schema-version-1 fixture is
`evaluation/fixtures/incident-click-search-public.json`. Its recorded SHA-256
is:

`2634a72fca1c8702e0250b8948241ae0d0a6dceaf18e8f6b56039a5734944fe0`

The checksum is stored in a sidecar and compiled into the runner. The loader
requires both values and the actual bytes to agree before parsing.

The public Click identity is:

- repository: `click`;
- revision: `6eeb50e948ea136db145280f6f5dd52eca3fa7e5` (`8.4.1`);
- exact gold: `click/src/click/core.py:1284`;
- gold snippet SHA-256:
  `6f4394e966077bc34c53dc38e6b98823f478a8e6cb31195359cb43f70e0ef2c2`.

Before freezing, the pinned checkout and exact source line were manually
checked. The source gate produces one and only one `error_text` task. The
runner does not publish the raw Incident line or query; its query SHA-256 is
`7866062172bda6b74e1315bb8c9bd77319533c363f314b3407790803be743e60`.

## Offline runner evidence

The runner test was written first and initially failed at collection because
`evaluation.replay_incident_search` did not exist. The implementation then
reused the existing boundaries:

```text
IncidentInput + IncidentExtractionCandidate
  -> validate_incident_extraction
  -> map_incident_to_retrieval_context
  -> ToolCallDecision (runner-built, not model-built)
  -> ToolStepExecutor
  -> ToolRouter
  -> PydanticToolAdapter
  -> injected search_code
  -> next ContextState / next ModelInput
```

The final focused run passed `37` tests. Fake execution proves:

- exact frozen arguments reach `search_code` once;
- `get_file_context`, model, and Agent Loop counts are all zero;
- budget changes from `1` to `0` after success or classified failure;
- Trace is exactly `tool_call -> tool_result`;
- errors, timeouts, non-JSON returns, malformed responses, and missing gold do
  not retry;
- the frozen gold can be found by rank without requiring it to be Top-1;
- the new Tool fact enters the next state and next model input while the
  original Incident source fact remains traceable;
- checksum, source, repo, revision, gold, argument, and nested in-memory
  decision drift fail before a Tool call.

This runner directly exercises the Tool backend adapter path. It does not use
or claim MCP Client transport.

## Real preflight and zero-call stop

The preflight CLI validates fixture bytes before checking the environment. It
then requires explicit `ZOEKT_URL`, `REPOSITORY_ROOT`, and
`ZOEKT_INDEX_REVISION` configuration before checking checkout revision,
`zoekt.name`, and the pinned local gold. It performs no network or Tool probe.

The 2026-08-29 preflight returned the stable category
`zoekt_url_missing`. Per the frozen Gate, execution stopped immediately:

| Boundary | Count |
| --- | ---: |
| real `search_code` | 0 |
| `get_file_context` | 0 |
| model | 0 |
| Agent Loop | 0 |
| network preflight probe | 0 |
| Trace events | 0 |

No default endpoint was assumed, no query or fixture was changed, and no
second attempt exists.

## Offline verification after the block

| Check | Result |
| --- | --- |
| Incident context + new runner focus | `37 passed` |
| full `pytest` | `321 passed` |
| retrieval data | `18` cases valid |
| Agent data | `10` cases valid; frozen hash unchanged |
| holdout data | `10` cases valid (`8/2`); both hashes unchanged |
| fixture checksum | passed |
| package imports | passed |
| `git diff --check` | passed |
| changed-file content scan | no matches |

The content scan covered the fixture, checksum, runner, tests, design/README
changes, and these reports. It checked local user-directory paths, email
shapes, credential-like prefixes/assignments, private-key headers, and the
supplied provider name/endpoint. This is a pattern scan, not a proof that
arbitrary unknown secrets cannot exist. Git history and author metadata are
outside its scope.

## Honest boundary and next step

The fixture, offline runner, tests, and reports are implementation evidence,
not proof of a real search result. There is no observed gold rank from Zoekt,
no next-state fact from a real backend, no root cause, and no Incident answer.

Restore the three explicit Zoekt configuration items, rerun the same zero-Tool
preflight, and only if every check passes execute the unchanged fixture once.
Success, Tool error, or timeout must all end that sole real attempt without a
query, gold, limit, language, or path change.
