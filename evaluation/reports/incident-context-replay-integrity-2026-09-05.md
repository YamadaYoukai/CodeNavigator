# Incident Context Replay Artifact Integrity Follow-up — 2026-09-05

## Result

**PASSED after a second follow-up.** Commit `82768d1` closed the initially
identified execution-artifact validator gaps after commit `95a626d`, but an
independent re-review found one remaining overlap between the success and
semantic-failure contracts. The subsequent fix makes those states disjoint.
Both follow-ups validate the unchanged 2026-09-02 context replay artifact and
do not replace, rewrite, or rerun that real execution.

No Tool, model, Agent Loop, MCP transport, or network service was called. The
only checkout-dependent operation was a read-only, zero-Tool preflight against
the pinned local Click source.

## Reproduced validator bypasses

Before the fix, the public `validate_execution_artifact()` accepted all three
independent in-memory mutations of the checked-in success artifact:

- `tool_result_sha256="not-a-digest"`;
- a different, well-formed 64-character lowercase digest;
- a reported failure with `error_type="fabricated_error"`, a successful
  ToolResult, and no context match.

The focused baseline was `73 passed`. After adding the new negative cases but
before changing the validator, the context replay test module reported
`7 failed, 41 passed`. The failures also exposed impossible combinations in
which semantic errors were attributed to a failed ToolResult or stable Tool
errors were attributed to a successful ToolResult.

After `82768d1`, the public validator still accepted both `unexpected_result`
and `context_mismatch` when the successful ToolResult retained the fixed,
independently verified success digest
`d6a654cc31e1efd119f206da9d949332aa8cc206f6d8449645d506dfdc337818`
but the report was changed to `status="error"` with no context match. The
targeted second-round red step reported `2 failed, 48 passed`.

## Narrow fix

The follow-up changes only the saved-artifact contract:

- every non-null context Tool-result digest must match
  `^[0-9a-f]{64}$`;
- a reported success must use the independently recomputed fixed digest
  `d6a654cc31e1efd119f206da9d949332aa8cc206f6d8449645d506dfdc337818`;
- a failed ToolResult may use only `tool_execution_error` or `tool_timeout`,
  with matching report and ToolResult errors and no result digest or context
  match;
- a successful ToolResult that fails result inspection may use only
  `unexpected_result` or `context_mismatch`, with a well-formed result digest
  that differs from the fixed success digest, and no context match.

The last condition is the second-round correction: a result already proven to
be the fixed successful result cannot simultaneously claim a semantic failure.
An independently valid but different digest remains reachable for inspection
failure coverage.

The Tool execution path, fixed arguments, `0/1/0/0` call counts, `1 -> 0`
budget, Trace, context window, source chain, fixture, upstream search artifact,
and real context artifact are unchanged.

## Independent hash and mutation probes

A standalone read of the pinned Click checkout rebuilt the fixed `1264..1304`
context and canonical Tool result without importing or calling the Tool:

| Value | Independently recomputed SHA-256 |
| --- | --- |
| context content | `33955d1d88da4656c038ed3e3b4e5a24201b57477d95bf9ab15cbc466bf728d4` |
| complete Tool result | `d6a654cc31e1efd119f206da9d949332aa8cc206f6d8449645d506dfdc337818` |

A separate first-round in-memory probe passed malformed digest, wrong
well-formed digest, fabricated semantic error, and fabricated Tool error
artifacts directly to `validate_execution_artifact()`. All four were rejected
as `invalid_context_execution_artifact`. That probe did not cover the fixed
success digest relabeled as a stable semantic error.

The second-round probe passes that exact fixed digest with each of
`unexpected_result` and `context_mismatch` through the same public validator.
Both are now rejected as `invalid_context_execution_artifact`; the existing
different-digest semantic-failure cases remain accepted.

## Verification

- context replay module: `50 passed`;
- Incident context/search/context-suffix focus: `87 passed`;
- complete suite: `371 passed`;
- retrieval, Agent, and holdout datasets: valid at `18/10/10` cases;
- context replay `--validate-only`: valid with all Tool/model/loop calls `0`;
- zero-Tool local preflight: all seven checks passed;
- package import, `git diff --check 82768d1`, and frozen artifact hashes: passed.

The unchanged hashes remain:

- context execution artifact:
  `dae39f4618cb15d0f353db4d5c5bc131b524be2aa226970f1908b4f161646545`;
- Incident fixture:
  `2634a72fca1c8702e0250b8948241ae0d0a6dceaf18e8f6b56039a5734944fe0`;
- successful search artifact:
  `41607f1a163440d3b517bb05e637f15dc8c1924b11c26323b9f91f57a5500acf`.

## Boundary

This establishes fail-closed offline evidence for one fixed public Click
search-to-context chain. It does not establish two Tools in one Trace,
model-selected orchestration, root-cause quality, retries, failure recovery,
multiple Incident cases, or a complete Incident Copilot.
