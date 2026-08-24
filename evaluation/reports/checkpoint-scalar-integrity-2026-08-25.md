# Checkpoint JSON-scalar integrity repair evidence

## Result

- Executed on: `2026-08-25` (Asia/Shanghai).
- Baseline: clean `8db66aeccf79b2b0f65a97223ca2264b9dd6d3cd`.
- Verdict: the newly reported scalar-type bypass is reproduced and repaired.
- Intended commit subject: `fix: reject checkpoint scalar type drift`. The
  resulting commit ID is reported in the final handoff because a commit cannot
  contain its own hash.

## Pre-repair counterexamples

On the unchanged baseline, two fresh temporary resumable records were modified
only in the recorded `ToolCall`:

- `literal: true` became integer `1`;
- integer `limit: 3` became floating-point `3.0`.

For each mutation, the file store accepted the record, resume called the model
once and no Tool, and the task produced a completed record. The same two
mutations were also accepted when applied to completed records.

The root cause was ordinary Python mapping equality in the shared Tool
correlation helper. Python considers `True == 1` and `3 == 3.0`, so both the
direct equality fast path and the comparison after a legitimate repository
alias rewrite were type-insensitive.

## Repaired contract

- Tool argument comparison now walks JSON objects and arrays recursively.
- JSON object key sets and array order must match.
- Scalar runtime type and value must both match; booleans, integers, and
  floating-point values are distinct even when Python considers them equal.
- The same comparator is used for the exact-original path and the post-resolver
  path, and the existing shared Tool helper still validates resumable and
  completed traces.
- Tests cover both scalar mutations through direct loading, alias resolution,
  completed-record loading, and `AgentLoop.resume`. Invalid resumable files now
  fail before a new event, model call, or Tool call (`model=0`, `Tool=0`).

## Acceptance evidence

- Before implementation, the expanded focused suite produced exactly
  `8 failed, 50 passed`.
- After implementation, the focused checkpoint/resume suite produced
  `58 passed`.
- Full regression: `249 passed`.
- Retrieval dataset validation: `18` cases valid.
- Agent dataset validation: `10` cases valid; SHA-256
  `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129`.
- Holdout dataset validation: `10` cases valid; SHA-256
  `e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396`;
  exclusion SHA-256
  `716e244d016bfe9a53e1b01410faeef26db7d526ef1d6905c24dbca6e8623e3e`.
- Package import, Diff checks, and the baseline commit check passed.

## Fresh two-process proof

The offline runner used a new temporary output directory and again launched two
independent Python processes:

- Process A exited `75`; Process B exited `0`.
- The exact saved next `ModelInput` was consumed.
- Tool executions across both processes remained `1`.
- The Trace retained one Session, one successful Tool pair, continuous
  sequences `1..10`, and one terminal.
- Resumable checkpoint SHA-256:
  `cae98afa691ce6d874d5fb2fced1ba4650901be19411511dbf41265e0715ad03`.
- Completed record SHA-256:
  `19ecf5c0fa34dcba9e0b2a9a7c57ae2465f0064beb5db13af611b916fa7f4079`.

The hashes still match the earlier normal-path proof because the repair changes
only validation of inconsistent payloads.

## Honest boundary

- This correction invalidates the standalone pass claim made for `8db66ae`; it
  does not rewrite or hide that earlier evidence.
- No real model or Zoekt service was called.
- Recovery remains limited to the boundary after a recorded successful
  ToolResult.
- Pending model requests, executing Tools, failed Tool results, arbitrary crash
  points, concurrent workers, and exactly-once external side effects remain out
  of scope.
- No credential, endpoint, provider payload, runtime checkpoint path, or local
  absolute path is recorded here.
