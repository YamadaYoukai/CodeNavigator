"""Offline scripted candidate and independent mutation probes; never executes tools.

Run with ``python -m evaluation.validate_incident_analysis``. JSON is printed to
stdout; this runner itself never writes files and does not import test helpers.
"""
from __future__ import annotations

import json
from copy import deepcopy

from evaluation.replay_incident_context import load_context_replay, validate_execution_artifact
from src.examples.code_understanding_agent.incident_analysis import (
    DEFAULT_CONTEXT_ARTIFACT_PATH, validate_incident_analysis,
)


def main() -> None:
    replay = load_context_replay()
    fixture = replay.fixture
    source = fixture.incident_input.sources[0]
    gold = fixture.persisted.gold
    candidate = {
        'case_id': fixture.persisted.case_id,
        'facts': [
            {'kind': 'incident', 'fact_id': 'log-observation', 'observation': {
                'value': source.text, 'source_ids': [source.source_id]}},
            {'kind': 'code', 'fact_id': 'code-observation', 'citation': {
                'repo': gold.repo, 'path': gold.path, 'line': gold.line}, 'snippet': gold.snippet},
        ],
        'hypotheses': [{'hypothesis_id': 'extra-argument',
                        'statement': 'The invocation may supply an unconsumed argument.',
                        'supporting_fact_ids': ['log-observation', 'code-observation'],
                        'confidence': 'low'}],
        'missing_information': [{'information_id': 'invocation',
                                 'question': 'What exact invocation, command declaration and Click version produced the log?'}],
        'investigation_steps': [{'step_id': 'compare-arguments',
                                 'action': 'Compare the supplied arguments with the command declaration.',
                                 'related_hypothesis_ids': ['extra-argument']}],
    }
    result = validate_incident_analysis(json.dumps(candidate))
    assert validate_incident_analysis(result.to_json()).to_json() == result.to_json()
    insufficient = deepcopy(candidate)
    insufficient['hypotheses'] = []
    insufficient['investigation_steps'][0]['related_hypothesis_ids'] = []
    safe_result = validate_incident_analysis(json.dumps(insufficient))
    probes = [
        (('facts', 0, 'observation', 'source_ids'), ['fabricated-source']),
        (('facts', 1, 'citation', 'repo'), 'other'),
        (('facts', 1, 'citation', 'path'), 'src/other.py'),
        (('facts', 1, 'citation', 'line'), gold.line + 1),
        (('facts', 1, 'snippet'), 'fabricated'),
        (('hypotheses', 0, 'supporting_fact_ids'), ['other-case-fact']),
        (('investigation_steps', 0, 'related_hypothesis_ids'), ['other-case-hypothesis']),
        (('hypotheses', 0, 'confidence'), 'certain'),
    ]
    rejected = []
    for path, replacement in probes:
        changed = deepcopy(candidate)
        target = changed
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = replacement
        try:
            validate_incident_analysis(json.dumps(changed))
        except ValueError:
            rejected.append('.'.join(map(str, path)))
        else:
            raise AssertionError(f'accepted mutation: {path}')
    scalar_rejections = []
    for value in (True, 1.0):
        artifact = json.loads(DEFAULT_CONTEXT_ARTIFACT_PATH.read_bytes())
        artifact['replay']['calls']['get_file_context'] = value
        try:
            validate_execution_artifact(json.dumps(artifact).encode(), replay)
        except ValueError:
            scalar_rejections.append(type(value).__name__)
        else:
            raise AssertionError(f'accepted scalar drift: {value!r}')
    print(json.dumps({
        'status': 'passed', 'candidate': result.to_payload(),
        'insufficient_evidence': safe_result.to_payload(),
        'rejected_analysis_mutations': rejected,
        'rejected_context_scalar_mutations': scalar_rejections,
        'calls': {'model': 0, 'search_code': 0, 'get_file_context': 0, 'agent_loop': 0},
        'boundary': 'Scripted structure and provenance only; no causal quality or confidence calibration.',
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
