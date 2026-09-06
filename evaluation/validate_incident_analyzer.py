"""Independent offline public-entry probes; no test helpers or real execution.

Run ``python -m evaluation.validate_incident_analyzer`` from the project root.
Prints deterministic JSON; redirect to a NEW file when saving evidence.
"""
from __future__ import annotations

import json
import socket
import subprocess
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from evaluation.replay_incident_context import DEFAULT_FIXTURE_PATH, DEFAULT_SEARCH_ARTIFACT_PATH
from src.examples.code_understanding_agent import (
    AgentLoop, FakeModel, OpenAIModel, ToolRouter, TracedModelClient,
    FakeIncidentAnalyzer, analyze_incident, load_incident_analysis_input,
)
from src.examples.code_understanding_agent.incident_analysis import (
    DEFAULT_CONTEXT_ARTIFACT_PATH, IncidentAnalysisCandidate, IncidentAnalysisResult,
)


@contextmanager
def blocked_execution():
    import src.server as server

    calls = {name: 0 for name in (
        'model', 'search_code', 'get_file_context', 'agent_loop',
        'tool_router', 'network', 'subprocess',
    )}

    def blocker(category):
        def forbidden(*args, **kwargs):
            calls[category] += 1
            raise AssertionError(f'offline analyzer attempted {category}')
        return forbidden

    targets = [
        (AgentLoop, 'run', 'agent_loop'), (AgentLoop, 'resume', 'agent_loop'),
        (FakeModel, 'decide', 'model'), (OpenAIModel, 'decide', 'model'),
        (TracedModelClient, 'decide', 'model'), (ToolRouter, 'execute', 'tool_router'),
        (server, 'search_code', 'search_code'), (server, 'get_file_context', 'get_file_context'),
        (socket, 'create_connection', 'network'), (socket, 'getaddrinfo', 'network'),
        (subprocess, 'Popen', 'subprocess'),
    ]
    targets.extend((socket.socket, name, 'network') for name in (
        'connect', 'connect_ex', 'send', 'sendall', 'sendto',
    ))
    with ExitStack() as stack:
        for owner, name, category in targets:
            stack.enter_context(patch.object(owner, name, blocker(category)))
        yield calls
        assert all(count == 0 for count in calls.values())


def run_checks() -> dict:
    source_input = load_incident_analysis_input()
    source = source_input.incident_input.sources[0]
    code = source_input.code_locations[0]
    script = IncidentAnalysisCandidate.model_validate_json(json.dumps({
        'case_id': source_input.case_id,
        'facts': [
            {'kind': 'incident', 'fact_id': 'log-observation', 'observation': {
                'value': source.text, 'source_ids': [source.source_id]}},
            {'kind': 'code', 'fact_id': 'code-observation',
             'citation': code.citation.model_dump(mode='json'), 'snippet': code.snippet},
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
    }))
    fakes = []

    def fake(*scripts):
        instance = FakeIncidentAnalyzer(scripts)
        fakes.append(instance)
        return instance

    normal = analyze_incident(fake(script))
    assert type(normal) is IncidentAnalysisResult
    insufficient = script.model_dump(mode='python')
    insufficient['hypotheses'] = ()
    insufficient['investigation_steps'][0]['related_hypothesis_ids'] = ()
    insufficient = IncidentAnalysisCandidate.model_validate(insufficient)
    safe = analyze_incident(fake(insufficient))
    assert not safe.hypotheses and safe.missing_information
    assert analyze_incident(fake(script)).to_json() == normal.to_json()

    rejected = []
    for label, path, value in (
        ('wrong_case', ('case_id',), 'other-case'),
        ('unknown_source', ('facts', 0, 'observation', 'source_ids'), ('invented',)),
        ('wrong_repo', ('facts', 1, 'citation', 'repo'), 'other'),
        ('wrong_path', ('facts', 1, 'citation', 'path'), 'src/other.py'),
        ('wrong_line', ('facts', 1, 'citation', 'line'), code.citation.line + 1),
        ('wrong_snippet', ('facts', 1, 'snippet'), 'fabricated'),
        ('forged_result', ('facts', 0, 'observation', 'value'), 'fabricated'),
    ):
        changed = deepcopy(script.model_dump(mode='python'))
        target = changed
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        cls = IncidentAnalysisResult if label == 'forged_result' else IncidentAnalysisCandidate
        analyzer = fake(cls.model_validate(changed))
        try:
            analyze_incident(analyzer)
        except ValueError:
            rejected.append(label)
        else:
            raise AssertionError(f'accepted {label}')
        assert analyzer.call_count == 1

    tampered = IncidentAnalysisResult.model_validate(script.model_dump(mode='python'))
    object.__setattr__(tampered.hypotheses[0], 'confidence', 'certain')
    try:
        analyze_incident(fake(tampered))
    except ValueError:
        rejected.append('tampered_result_structure')
    else:
        raise AssertionError('trusted tampered Result')

    pre_call_rejections = []
    with TemporaryDirectory() as directory:
        for kind, original in (
            ('fixture', DEFAULT_FIXTURE_PATH), ('search', DEFAULT_SEARCH_ARTIFACT_PATH),
            ('context', DEFAULT_CONTEXT_ARTIFACT_PATH),
        ):
            changed = json.loads(original.read_bytes())
            if kind == 'fixture':
                changed['incident_input']['sources'][0]['text'] = 'fabricated'
            elif kind == 'search':
                changed['replay']['gold_match']['line'] += 1
            else:
                changed['replay']['calls']['get_file_context'] = 1.0
            path = Path(directory) / original.name
            path.write_text(json.dumps(changed), encoding='utf-8')
            if kind == 'fixture':
                path.with_suffix('.json.sha256').write_bytes(original.with_suffix('.json.sha256').read_bytes())
            key = 'fixture_path' if kind == 'fixture' else kind + '_artifact_path'
            analyzer = fake(script)
            try:
                analyze_incident(analyzer, **{key: path})
            except ValueError:
                pre_call_rejections.append(kind)
            else:
                raise AssertionError(f'accepted altered {kind}')
            assert analyzer.call_count == 0 and analyzer.remaining_candidates == 1

    adapter_calls = {'wrong_type': 0, 'exception': 0, 'pollution': 0, 'capture': 0}

    class WrongType:
        def analyze(self, analysis_input):
            adapter_calls['wrong_type'] += 1
            return script.to_payload()

    try:
        analyze_incident(WrongType())
    except TypeError:
        rejected.append('wrong_return_type')
    else:
        raise AssertionError('accepted wrong return type')
    assert adapter_calls['wrong_type'] == 1

    error = RuntimeError('scripted analyzer exception')

    class Broken:
        def analyze(self, analysis_input):
            adapter_calls['exception'] += 1
            raise error

    try:
        analyze_incident(Broken())
    except RuntimeError as caught:
        assert caught is error
        rejected.append('analyzer_exception')
    else:
        raise AssertionError('swallowed analyzer exception')
    assert adapter_calls['exception'] == 1

    class Polluting:
        def analyze(self, analysis_input):
            adapter_calls['pollution'] += 1
            object.__setattr__(analysis_input.incident_input.sources[0], 'text', 'fabricated')
            changed = script.model_copy(deep=True)
            object.__setattr__(changed.facts[0].observation, 'value', 'fabricated')
            return changed

    original_input = source_input.to_json()
    try:
        analyze_incident(Polluting())
    except ValueError:
        rejected.append('input_pollution')
    else:
        raise AssertionError('used polluted validation input')
    assert adapter_calls['pollution'] == 1
    assert load_incident_analysis_input().to_json() == original_input

    constructor_argument = script.model_copy(deep=True)
    isolated = fake(constructor_argument, constructor_argument)
    object.__setattr__(constructor_argument.facts[0].observation, 'value', 'changed')

    class Capturing:
        def analyze(self, analysis_input):
            adapter_calls['capture'] += 1
            self.input = analysis_input
            self.candidate = isolated.analyze(analysis_input)
            return self.candidate

    capturing = Capturing()
    result = analyze_incident(capturing)
    object.__setattr__(capturing.input.code_locations[0].citation, 'line', 1)
    object.__setattr__(capturing.candidate.facts[0].observation, 'value', 'changed')
    exported = isolated.analysis_inputs[0]
    object.__setattr__(exported.incident_input.sources[0], 'text', 'changed')
    result_payload = result.to_payload()
    result_payload['facts'].clear()
    assert isolated.analysis_inputs[0].to_json() == original_input
    assert result.to_json() == normal.to_json()
    assert analyze_incident(isolated).to_json() == normal.to_json()
    sequence = fake(script, insufficient)
    assert analyze_incident(sequence).to_json() == normal.to_json()
    assert analyze_incident(sequence).to_json() == safe.to_json()
    try:
        analyze_incident(sequence)
    except RuntimeError as caught:
        assert 'no scripted candidates' in str(caught)
        rejected.append('script_exhaustion')
    else:
        raise AssertionError('reused exhausted script')
    assert sequence.call_count == 3 and sequence.remaining_candidates == 0

    return {
        'status': 'passed', 'input': source_input.to_payload(),
        'normal': normal.to_payload(), 'insufficient_evidence': safe.to_payload(),
        'rejected_after_call': rejected,
        'rejected_before_call': pre_call_rejections,
        'checks': {'deterministic_json': True, 'constructor_isolation': True,
                   'input_snapshot_isolation': True, 'snapshot_export_isolation': True,
                   'returned_candidate_isolation': True, 'trusted_result_isolation': True,
                   'ordered_script': True, 'single_attempt_no_retry': True},
        'fake_calls': sum(instance.call_count for instance in fakes),
        'custom_adapter_calls': adapter_calls,
        'boundary': 'One scripted Click case; structure, provenance and isolation only. No causal quality assessment.',
    }


def main() -> None:
    with blocked_execution() as calls:
        report = run_checks()
    report['calls'] = calls
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
