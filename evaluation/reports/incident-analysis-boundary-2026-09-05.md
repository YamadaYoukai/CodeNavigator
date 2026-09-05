# Offline Incident analysis boundary — 2026-09-05

## Initial outcome: branch B, gate failed (resolved below)

The initial attempt retained the four-section design and a minimal independent Candidate/validator/Result
implementation as uncommitted work, together with the failing
regression tests. This is not an accepted output boundary. No candidate commit
was formed. M4 remains 30% and overall progress remains 60.5%.

Baseline HEAD was exactly `0094d46`; the only pre-existing untracked main-project
file was `DESIGN-zh_cn.md`, which was neither modified nor staged. Existing
Incident/context tests including extraction passed: 122 (87 without extraction).

## Failure and independent reproduction

The public context `validate_execution_artifact` accepts both JSON `true` and
`1.0` for `replay.calls.get_file_context`, returning integer `1`. The model uses
`Literal[1]`; strict model configuration does not preserve scalar identity for
that literal under the installed Pydantic version. This violates Gate 2 before
an analysis result can be trusted. No second hash/type boundary was added to
hide the upstream problem, and the existing validator was not changed.

A standalone probe, without importing the new module or its tests, loaded the
frozen context artifact, replaced that field with each value in memory, and
called the public validator with `load_context_replay()`. Both were accepted.

Reproduction (run from the project root with the project Python):

```python
import json
from pathlib import Path
from evaluation.replay_incident_context import load_context_replay, validate_execution_artifact
path = Path('evaluation/reports/incident-context-replay-real-2026-09-02.json')
for value in (True, 1.0):
    payload = json.loads(path.read_text())
    payload['replay']['calls']['get_file_context'] = value
    result = validate_execution_artifact(json.dumps(payload).encode(), load_context_replay())
    print(type(value).__name__, repr(value), repr(result.replay.calls.get_file_context))
```

## Verification

- Red phase: after introducing a nonvalidating API scaffold, all 58 new tests
  failed. An earlier absent-module collection error was not treated as the red gate.
- New module after implementation: 56 passed, 2 failed.
- Full requested Incident focus: 178 passed, 2 failed.
- Full suite: 427 passed, 2 failed. Both failures are the retained scalar probes.
- Three validate-only datasets: 18/10/10 valid.
- Context runner `--validate-only`: valid, all four execution counts zero.
- Valid scripted Click candidate and empty-hypothesis/missing-information branch
  passed. Those local successes do not override the failed source boundary.
- Import, diff whitespace and scoped sensitive-content checks passed.

No real model, search_code, get_file_context, Agent Loop, MCP transport or
network service was executed for this work. Tests use offline/fake paths.
The saved context's historical get_file_context count of 1 is not a new call.

Frozen bytes remained unchanged:

| Input | SHA-256 |
| --- | --- |
| Incident fixture | `2634a72fca1c8702e0250b8948241ae0d0a6dceaf18e8f6b56039a5734944fe0` |
| 08-30 search artifact | `41607f1a163440d3b517bb05e637f15dc8c1924b11c26323b9f91f57a5500acf` |
| 09-02 context artifact | `dae39f4618cb15d0f353db4d5c5bc131b524be2aa226970f1908b4f161646545` |

## Next action and limits

Continue the same boundary on the next actual work day: first repair scalar
identity enforcement in the existing public context artifact validator, with
failure tests and no alteration of frozen data, then rerun this analysis gate.
Do not claim causal correctness, calibrated confidence, production diagnosis
or completion of the planned broader independent analysis mutation matrix.
That final acceptance stage was stopped after the upstream failure.

Resume and recruiting activity were excluded by the user. No resume edits,
applications or messages were made or counted.


## Follow-up outcome: branch A, offline gate passed

The user authorized continuing the repair. The existing public context artifact
validator now compares the canonical SHA-256 of the decoded JSON with that of
the parsed model, using its existing canonicalizer. This catches scalar type
coercion before outcome invariants are trusted, while preserving object-key
order independence and array order. No parallel artifact validator was added.
The saved success artifact and reachable failure contracts remain accepted.

Twelve direct regression cases (all four call counts and both budget fields,
each with bool/float substitution) failed before the fix and passed afterward.
The independent analysis validator retains the four-section contract, exact
excerpt provenance gate and strict single-line citation model; source inputs
are reloaded and validated on each call. Candidate mappings/models are copied
and re-parsed, results are frozen detached snapshots, and exported JSON keeps
step order. No generic FinalAnswerDecision, Tool, checkpoint or loop was changed.

Final checks:

- analysis tests: 63 passed, including execution/network tripwires;
- requested Incident/context focus: 197 passed;
- full suite: 446 passed;
- datasets: 18/10/10 valid; context validate-only valid, all calls zero;
- standalone runner: 8/8 analysis mutations rejected (source, repo, path, line,
  snippet, fact link, hypothesis link, confidence), plus 2/2 upstream scalar
  mutations rejected without importing test helpers;
- scripted positive result and the no-hypothesis missing-information branch
  are saved in `incident-analysis-offline-2026-09-05.json`;
- import, diff whitespace, scoped sensitive-content scan and all three original
  frozen hashes passed. Frozen artifacts and the user's Chinese design file
  remain unchanged.

Reproduce the independent candidate/probes without writes or real execution:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m evaluation.validate_incident_analysis
```

The earlier branch-B findings are preserved as history, superseded by these
results. A single local candidate commit contains the contract and its required
source-boundary repair; no push, amend or history rewrite is performed.
M4 remains 30%, overall progress 60.5%. This verifies structure, source linkage
and deterministic offline representation on one Click case, not causal
entailment, confidence calibration, model extraction or production quality.
Next action: independently review this contract before deciding whether to
connect a provider-independent analyzer on the same fixed case.
