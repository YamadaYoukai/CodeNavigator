# First Real Incident Search Attempt — 2026-08-29

## Result

**FAILED CLOSED (branch B).** After the earlier environment block, the user
supplied the missing Zoekt endpoint configuration and explicitly requested the
real search. The unchanged frozen fixture passed the complete zero-Tool
preflight. The runner then executed exactly one real
`src.server.search_code` attempt, which ended as the stable Router category
`tool_execution_error`.

There was no retry, query change, alternate fixture, second Tool, model call,
or Agent Loop. M4 remains `10%`, total roadmap progress remains `56.5%`, and
this result does not support a successful Incident retrieval claim.

## Frozen input identity

- case: `incident-click-unexpected-extra-argument-001`;
- fixture SHA-256:
  `2634a72fca1c8702e0250b8948241ae0d0a6dceaf18e8f6b56039a5734944fe0`;
- Click revision: `6eeb50e948ea136db145280f6f5dd52eca3fa7e5`;
- canonical repository: `click`;
- frozen gold: `click/src/click/core.py:1284`;
- gold snippet SHA-256:
  `6f4394e966077bc34c53dc38e6b98823f478a8e6cb31195359cb43f70e0ef2c2`;
- query SHA-256:
  `7866062172bda6b74e1315bb8c9bd77319533c363f314b3407790803be743e60`.

The input, extraction candidate, repository selection, task rule, search
arguments, revision, and gold are byte-for-byte unchanged from the earlier
freeze. The report does not publish the raw Incident/query line, configured
endpoint, local repository root, or credential values.

## Zero-Tool preflight

Before the real attempt, the same runner rechecked:

| Check | Result |
| --- | --- |
| fixture bytes and recorded SHA-256 | passed |
| checkout revision | passed |
| declared index revision | passed |
| `zoekt.name=click` | passed |
| pinned local gold source line | passed |
| explicit endpoint configuration present | passed |

Preflight made no Tool or network probe.

## Sole real attempt

The allowlisted artifact is
`evaluation/reports/incident-search-replay-real-2026-08-29.json`. Its SHA-256
is:

`9eabb163bbc9af801b72ce6d81f37ffd72fd1f39a91442c6d522466707d9ade1`

Offline parsing of that saved artifact produced:

| Field | Observed result |
| --- | --- |
| status | `error` |
| stable error | `tool_execution_error` |
| real `search_code` | `1` |
| `get_file_context` | `0` |
| model | `0` |
| Agent Loop | `0` |
| budget | `1 -> 0` |
| Trace | `tool_call -> tool_result` |
| gold match | absent because no successful search payload exists |
| new stable Tool fact in next state/input | present |
| original Incident source fact preserved | yes |

The Router intentionally discarded backend exception detail. After the
attempt, a read-only local socket-table check found no listening process on the
configured port. It is therefore reasonable to infer that local service
unavailability caused or contributed to the stable execution failure, but
this is an inference rather than a captured backend exception or a successful
Zoekt health check. No network request was made to confirm it, because the
frozen runbook forbids an immediate retry or extra probe after the sole real
attempt.

## Offline verification

| Check | Result |
| --- | --- |
| saved real artifact strict parsing and invariant recomputation | passed |
| Incident context + replay focus | `37 passed` |
| full `pytest` | `321 passed` |
| retrieval data | `18` cases valid |
| Agent data | `10` cases valid; hash unchanged |
| holdout data | `10` cases valid (`8/2`); hashes unchanged |
| fixture checksum and package imports | passed |
| `git diff --check` | passed |
| new fixture/runner/test/artifact/report content scan | no matches |

The scan covered local user-directory paths, email shapes, credential-like
prefixes and assignments, private-key headers, and the supplied provider and
endpoint values. Pre-existing README examples were excluded from the
added-line scan. Git history and author metadata remain outside this check.

## Honest boundary and next step

This run proves fail-closed one-attempt semantics through the direct Tool
backend boundary. It does not prove a working Zoekt result, gold rank, MCP
Client transport, file-context retrieval, root cause, confidence, or an
Incident answer.

Do not execute this fixture again on 2026-08-29. On the next actual workday,
first restore and independently verify the local Zoekt listener and the same
pinned index identity without changing the fixture. A later real attempt
requires a new explicit execution decision and must still use this exact
fixture, query, repository, arguments, revision, and gold.
