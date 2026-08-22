# M4 first durable recovery increment — execution record

## Result

- Actual execution date: `2026-08-23` (Asia/Shanghai).
- Baseline: clean `e28419eabf4c056f7016e78804eca8f60776ff31`, with `191 passed` before implementation.
- Verdict: passed the first controlled M4 recovery increment.
- Progress update permitted by the frozen rule: M4 `0% -> 10%`; overall job-search path `54.5% -> 56.5%`.
- Commit subject: `feat: resume agent loop from durable checkpoint`; the resulting commit ID is reported in the final handoff because a commit cannot contain its own hash.
- Git author uses `@didiglobal.com`; this is local technical evidence, not a claim of public publication acceptance.

## Implemented boundary

- Schema-version-1 strict `resumable` and `completed` records, including exact state, next model input, budget, step, generation, and full trace prefix.
- Same-directory mode-`0600` temporary file, file fsync, atomic replace, and directory fsync.
- Successful-Tool checkpoint hook and `AgentLoop.resume`, with store re-read and all validation completed before any new model or Tool invocation.
- Terminal replacement with a non-resumable `completed` record.
- Focused fail-closed tests for corrupt or unknown JSON, task/sequence/budget/phase mismatches, dangling model and Tool events, error Tool results, finalized resumable records, stale generations, lost protected facts, and repeated resume.

## Independent-process proof

- Process A exit: `75` (`checkpoint_saved_then_interrupted`).
- Prefix before recovery: `session -> step -> model_request -> model_result -> tool_call -> tool_result`.
- Process B exit: `0` (`completed`).
- Final sequence: `session -> step -> model_request -> model_result -> tool_call -> tool_result -> step -> model_request -> model_result -> final_answer`.
- Tool executions across both processes: `1`; Session count: `1`; terminal count: `1`.
- Process B received the exact saved next `ModelInput`, including the fresh Tool fact at evidence budget zero.
- Final reason: `completed`; final evidence: `click/src/click/types.py:491`.
- Resumable checkpoint SHA-256: `cae98afa691ce6d874d5fb2fced1ba4650901be19411511dbf41265e0715ad03`.
- Completed record SHA-256: `19ecf5c0fa34dcba9e0b2a9a7c57ae2465f0064beb5db13af611b916fa7f4079`.
- JSON proof SHA-256: `e749dd4ef30a4f42ab13f00949136a5d0939a93c5c63b60c0ef6789d5482d695`.
- Markdown proof SHA-256: `042633946cbf7721fd7efcc587d61ed5771b138db8450d25013b02e6de35b4ad`.

## Acceptance evidence

- Focused checkpoint/resume suite: `26 passed`.
- Full regression: `217 passed`.
- Retrieval dataset validation: `18` cases valid.
- Agent dataset validation: `10` cases valid; SHA-256 `09b3e346686d550c09c3b361ec16d00e51cac8fd257850593901ab212c2da129`.
- Holdout dataset validation: `10` cases valid; SHA-256 `e08cd744a44b3fb99ac98d0f5bf77ef6489f324c8109aba980687f459a0df396`; exclusion SHA-256 `716e244d016bfe9a53e1b01410faeef26db7d526ef1d6905c24dbca6e8623e3e`.
- Package import and `git diff --check`: passed.
- Evidence scan found no credential, endpoint, runtime checkpoint path, or local absolute path in the two-process reports.

## Honest remaining scope

- M4 remains `90%` incomplete. This fixture is recovery evidence and is not one of the five final M4 incident cases.
- No real model or Zoekt call, retry, timeout policy, failure injection, structured incident input, log extraction, root-cause ranking, or Human-in-the-loop behavior was added.
- No claim is made for concurrent workers, arbitrary crash points, or exactly-once semantics for external Tool side effects.
- The next increment is structured Incident input plus log-field extraction, as scheduled after this recovery boundary.
