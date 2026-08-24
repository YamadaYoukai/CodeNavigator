# Checkpoint semantic-integrity repair evidence

> Correction recorded on `2026-08-25`: this report's original pass verdict was
> incomplete. Ordinary Python dictionary equality treated JSON `true` as equal
> to `1` and integer `3` as equal to floating-point `3.0`, so those two
> ToolCall-only type drifts still crossed both resumable and completed
> validation in commit `8db66ae`. The original evidence remains below as a
> historical record; the superseding counterexample, repair, and acceptance
> evidence are in `checkpoint-scalar-integrity-2026-08-25.md`.

## Result

- Executed on: `2026-08-24` (Asia/Shanghai).
- Baseline: clean `17d8ce634a3c6a4db445ab0ef29d433cab074119`.
- Verdict: the checkpoint semantic-integrity gate passed after a narrow
  follow-up repair.
- Intended commit subject: `fix: reject inconsistent checkpoint traces`.
  The resulting commit ID is reported in the final handoff because a commit
  cannot contain its own hash.

## Pre-repair counterexamples

Before changing code, a fresh temporary checkpoint reproduced all original
blockers:

- changing only the recorded `search_code.arguments.query` was accepted;
  `AgentLoop.resume` reached the model once (`model=1`, `Tool=0`);
- a completed record with different model-decision and recorded-Tool arguments
  was accepted;
- a completed record whose final model answer differed from `FinalAnswer.answer`
  was accepted.

The old 08-23 normal-path reports were read as baseline evidence and were not
deleted or rewritten.

## Repaired contract

- Resumable and completed traces call the same Tool-correlation helper.
- `call_id`, tool name, and all non-repository arguments must be exact.
- The only permitted argument change is the existing
  `RepositoryAliasResolver` result for `search_code.repo` or
  `get_file_context.repository`, recomputed from saved `repository_hints`.
- Exact canonical names and uniquely mapped exact aliases pass. Case changes,
  unknown names, ambiguous aliases, and changes to `query`, `path`, `limit`, or
  `literal` fail closed.
- A successful final model decision must exactly match `FinalAnswer` in answer,
  normalized evidence, uncertainties, next queries, and
  `termination_reason=completed`.
- Failure terminals reuse the stable constructor used by `AgentLoop`; model
  errors, invalid output, evidence rejection, exhausted budget, Tool errors,
  timeouts, and an explainable Harness invariant remain loadable only with the
  matching reason and stable public answer.
- The resume-side tamper test proves rejection before any new event, model call,
  or Tool call (`model=0`, `Tool=0`).

## Acceptance evidence

- Focused checkpoint/resume suite: `50 passed`.
- Full regression: `241 passed`.
- Retrieval dataset validation: `18` cases valid.
- Agent dataset validation: `10` cases valid; SHA-256
  `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129`.
- Holdout dataset validation: `10` cases valid; SHA-256
  `e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396`;
  exclusion SHA-256
  `716e244d016bfe9a53e1b01410faeef26db7d526ef1d6905c24dbca6e8623e3e`.
- Package import, `git diff --check 17d8ce6`, and
  `git show --check 17d8ce6`: passed.

## Fresh two-process proof

The offline runner wrote to a new temporary output directory and launched two
independent Python processes:

- Process A exited `75` immediately after durable generation 1.
- Process B exited `0`, received the exact saved next `ModelInput`, and wrote
  completed generation 2.
- The trace retained one Session, one successful Tool pair, continuous
  sequences `1..10`, and one terminal.
- Tool executions across both processes: `1`.
- A completed record remained non-resumable.
- Resumable checkpoint SHA-256:
  `cae98afa691ce6d874d5fb2fced1ba4650901be19411511dbf41265e0715ad03`.
- Completed record SHA-256:
  `19ecf5c0fa34dcba9e0b2a9a7c57ae2465f0064beb5db13af611b916fa7f4079`.

Both checkpoint hashes exactly match the 08-23 normal-path proof because the
serialized runtime payload did not change; the repair only rejects inconsistent
payloads during validation.

## Honest boundary

- No real model or Zoekt service was called.
- Only recovery after a recorded successful Tool result is implemented.
- Pending model requests, executing Tools, failed Tool results, arbitrary crash
  points, concurrent workers, and exactly-once external side effects remain out
  of scope.
- This synthetic fixture is recovery evidence, not one of the five final M4
  incident cases.
- Structured Incident input, log extraction, retries, failure injection, and
  new M4 features were not added.
- No credential, endpoint, provider payload, runtime checkpoint path, or local
  absolute path is recorded here.
