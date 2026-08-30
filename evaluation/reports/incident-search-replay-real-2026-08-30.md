# Successful Real Incident Search Replay — 2026-08-30

## Result

**PASSED (branch A).** Before this new, explicitly authorized attempt, a
read-only socket-table check found the expected local listener and the local
container inventory showed the fixed Zoekt service running. These checks did
not send a request to Zoekt. The unchanged frozen fixture then passed the
complete zero-Tool preflight, and exactly one real `src.server.search_code`
execution returned the frozen gold as Top-1.

There was no retry, alternate query, fixture or gold change, second Tool,
model call, or Agent Loop. The failed 2026-08-29 attempt remains preserved as
historical evidence; this successful attempt has its own dated artifact.

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

The fixture bytes, extraction candidate, repository selection, task rule,
search arguments, revision, and gold are unchanged from the earlier freeze.
The report does not publish the raw Incident/query line, configured endpoint,
local repository root, credentials, or backend exception detail.

## Zero-Tool preflight

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
`evaluation/reports/incident-search-replay-real-2026-08-30.json`. Its SHA-256
is:

`41607f1a163440d3b517bb05e637f15dc8c1924b11c26323b9f91f57a5500acf`

Offline parsing and invariant recomputation of that saved artifact produced:

| Field | Observed result |
| --- | --- |
| status | `success` |
| real `search_code` | `1` |
| `get_file_context` | `0` |
| model | `0` |
| Agent Loop | `0` |
| budget | `1 -> 0` |
| Trace | `tool_call -> tool_result` |
| gold rank | `1` |
| gold location | `click/src/click/core.py:1284` |
| gold snippet hash | `6f4394e966077bc34c53dc38e6b98823f478a8e6cb31195359cb43f70e0ef2c2` |
| Tool result hash | `af3be0e24a5c84950215d62178da6a0f6220e2bbaebc5f85f0339fbe69bb858e` |
| new Tool fact in next state/input | present |
| original Incident source fact preserved | yes |

The matched candidate was checked against the frozen relative path, exact line
number, repository, and source snippet. It is retrieval evidence, not a root
cause or troubleshooting recommendation.

## Offline verification

| Check | Result |
| --- | --- |
| saved artifact strict parsing and invariant recomputation | passed |
| Incident context + replay focus | `37 passed` |
| full `pytest` | `321 passed` |
| retrieval data | `18` cases valid |
| Agent data | `10` cases valid; hash unchanged |
| holdout data | `10` cases valid (`8/2`); hashes unchanged |
| fixture checksum and package imports | passed |
| `git diff --check` | passed |
| changed-file content scan | no matches |

The content scan covered local user-directory paths, email shapes,
credential-like prefixes and assignments, private-key headers, and the
supplied provider name and endpoint. Pre-existing README examples were
excluded from its added-line scan. Git history and author metadata are outside
this check; the post-commit `git show --check` remains a separate gate.

## Honest boundary and next step

This proves one deterministic, successful Incident-derived search through the
existing direct Tool backend boundary. It does not prove MCP Client transport,
`get_file_context`, model extraction or selection, an Agent Loop, root-cause
quality, retries, failure injection, or a complete Incident Copilot.

The next technical increment may add one public deterministic
`search_code -> get_file_context` evidence chain. It must not reinterpret this
single retrieval hit as broader Incident-answer quality.
