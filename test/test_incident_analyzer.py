"""Behavior tests for the public analyzer entry point, using only frozen inputs."""
import json
import pytest

from evaluation.replay_incident_context import DEFAULT_FIXTURE_PATH, DEFAULT_SEARCH_ARTIFACT_PATH
from src.examples.code_understanding_agent.incident_analysis import (
    DEFAULT_CONTEXT_ARTIFACT_PATH, IncidentAnalysisCandidate, IncidentAnalysisResult,
)
from src.examples.code_understanding_agent.incident_analyzer import (
    FakeIncidentAnalyzer, analyze_incident, load_incident_analysis_input,
)


def candidate(insufficient=False):
    value = {
        'case_id': 'incident-click-unexpected-extra-argument-001',
        'facts': [
            {'kind': 'incident', 'fact_id': 'log', 'observation': {
                'value': 'Got unexpected extra argument', 'source_ids': ['log-001']}},
            {'kind': 'code', 'fact_id': 'code', 'citation': {
                'repo': 'click', 'path': 'src/click/core.py', 'line': 1284},
             'snippet': '"Got unexpected extra argument ({args})",'},
        ],
        'hypotheses': [] if insufficient else [{
            'hypothesis_id': 'h', 'statement': 'An extra argument may be present.',
            'supporting_fact_ids': ['log', 'code'], 'confidence': 'low'}],
        'missing_information': [{'information_id': 'm', 'question': 'What invocation was used?'}],
        'investigation_steps': [{'step_id': 's', 'action': 'Compare the invocation with the declaration.',
                                 'related_hypothesis_ids': [] if insufficient else ['h']}],
    }
    return IncidentAnalysisCandidate.model_validate_json(json.dumps(value))


@pytest.fixture(autouse=True)
def prohibit_execution(monkeypatch):
    import socket
    import subprocess
    import src.server as server
    from src.examples.code_understanding_agent import (
        AgentLoop, FakeModel, OpenAIModel, ToolRouter, TracedModelClient,
    )

    def forbidden(*args, **kwargs):
        pytest.fail('offline analyzer attempted execution')

    for owner, name in [
        (socket.socket, 'connect'), (socket.socket, 'connect_ex'),
        (socket, 'create_connection'), (socket, 'getaddrinfo'),
        (subprocess, 'Popen'), (ToolRouter, 'execute'),
        (AgentLoop, 'run'), (AgentLoop, 'resume'),
        (FakeModel, 'decide'), (OpenAIModel, 'decide'), (TracedModelClient, 'decide'),
        (server, 'search_code'), (server, 'get_file_context'),
    ]:
        monkeypatch.setattr(owner, name, forbidden)


@pytest.mark.parametrize('insufficient', [False, True])
def test_entry_returns_detached_reproducible_trusted_result(insufficient):
    script = candidate(insufficient)
    fake = FakeIncidentAnalyzer([script])
    result = analyze_incident(fake)
    assert type(result) is IncidentAnalysisResult
    assert result.to_json() == script.to_json()
    assert analyze_incident(FakeIncidentAnalyzer([script])).to_json() == result.to_json()
    assert fake.call_count == 1 and fake.remaining_candidates == 0


def test_input_contains_only_verified_sources():
    fake = FakeIncidentAnalyzer([candidate()])
    analyze_incident(fake)
    value = fake.analysis_inputs[0].to_payload()
    assert value == {
        'case_id': 'incident-click-unexpected-extra-argument-001',
        'incident_input': {'sources': [{'source_id': 'log-001', 'source_type': 'log',
                                       'text': 'Error: Got unexpected extra argument'}]},
        'code_locations': [{'citation': {'repo': 'click', 'path': 'src/click/core.py', 'line': 1284},
                            'snippet': '"Got unexpected extra argument ({args})",'}],
    }


@pytest.mark.parametrize('kind', ['fixture', 'search', 'context'])
def test_source_tampering_rejected_before_analyzer(tmp_path, kind):
    original = {'fixture': DEFAULT_FIXTURE_PATH, 'search': DEFAULT_SEARCH_ARTIFACT_PATH,
                'context': DEFAULT_CONTEXT_ARTIFACT_PATH}[kind]
    value = json.loads(original.read_bytes())
    if kind == 'fixture':
        value['incident_input']['sources'][0]['text'] = 'fabricated'
    elif kind == 'search':
        value['replay']['gold_match']['line'] += 1
    else:
        value['replay']['calls']['get_file_context'] = True
    path = tmp_path / original.name
    path.write_text(json.dumps(value))
    if kind == 'fixture':
        path.with_suffix('.json.sha256').write_bytes(original.with_suffix('.json.sha256').read_bytes())
    key = 'fixture_path' if kind == 'fixture' else kind + '_artifact_path'
    fake = FakeIncidentAnalyzer([candidate()])
    with pytest.raises(ValueError):
        analyze_incident(fake, **{key: path})
    assert fake.call_count == 0 and fake.remaining_candidates == 1


@pytest.mark.parametrize('change', ['case', 'source', 'repo', 'path', 'line', 'snippet'])
@pytest.mark.parametrize('result_label', [False, True])
def test_entry_rejects_invalid_candidate_even_with_result_label(change, result_label):
    value = candidate().model_dump(mode='python')
    if change == 'case':
        value['case_id'] = 'another-case'
    elif change == 'source':
        value['facts'][0]['observation']['source_ids'] = ('invented',)
    elif change == 'snippet':
        value['facts'][1]['snippet'] = 'fabricated'
    else:
        value['facts'][1]['citation'][change] = {'repo': 'other', 'path': 'other.py', 'line': 1285}[change]
    cls = IncidentAnalysisResult if result_label else IncidentAnalysisCandidate
    # A valid schema/Result label says nothing about provenance.
    script = cls.model_validate(value)
    fake = FakeIncidentAnalyzer([script])
    with pytest.raises(ValueError):
        analyze_incident(fake)
    assert fake.call_count == 1


@pytest.mark.parametrize('field,value', [('confidence', 'certain'), ('confidence', True)])
def test_entry_reparses_tampered_result_structure(field, value):
    script = IncidentAnalysisResult.model_validate(candidate().model_dump(mode='python'))
    object.__setattr__(script.hypotheses[0], field, value)
    with pytest.raises(ValueError):
        analyze_incident(FakeIncidentAnalyzer([script]))


@pytest.mark.parametrize('value', [None, {}, 'answer', 1])
def test_wrong_return_type_is_not_success_or_retried(value):
    class WrongAnalyzer:
        calls = 0

        def analyze(self, analysis_input):
            self.calls += 1
            return value

    analyzer = WrongAnalyzer()
    with pytest.raises(TypeError, match='IncidentAnalysisCandidate'):
        analyze_incident(analyzer)
    assert analyzer.calls == 1


def test_valid_result_label_is_revalidated_and_detached():
    script = IncidentAnalysisResult.model_validate(candidate().model_dump(mode='python'))
    class Analyzer:
        def analyze(self, analysis_input):
            return script
    result = analyze_incident(Analyzer())
    saved = result.to_json()
    object.__setattr__(script.facts[0].observation, 'value', 'changed')
    assert result.to_json() == saved
    with pytest.raises(ValueError):
        analyze_incident(Analyzer())


@pytest.mark.parametrize('part', ['incident', 'code'])
def test_analyzer_cannot_poison_validation_sources(part):
    class PollutingAnalyzer:
        calls = 0

        def analyze(self, analysis_input):
            self.calls += 1
            script = candidate()
            if part == 'incident':
                object.__setattr__(analysis_input.incident_input.sources[0], 'text', 'fabricated')
                object.__setattr__(script.facts[0].observation, 'value', 'fabricated')
            else:
                object.__setattr__(analysis_input.code_locations[0].citation, 'line', 1285)
                object.__setattr__(script.facts[1].citation, 'line', 1285)
            return script

    before = load_incident_analysis_input().to_json()
    analyzer = PollutingAnalyzer()
    with pytest.raises(ValueError):
        analyze_incident(analyzer)
    assert analyzer.calls == 1
    assert load_incident_analysis_input().to_json() == before


def test_script_constructor_is_detached():
    script = candidate()
    saved = script.to_json()
    scripts = [script]
    fake = FakeIncidentAnalyzer(scripts)
    scripts.clear()
    object.__setattr__(script.facts[0].observation, 'value', 'fabricated')
    assert analyze_incident(fake).to_json() == saved


def test_fake_input_export_and_returned_candidate_are_detached():
    script = candidate()
    expected = script.to_json()
    fake = FakeIncidentAnalyzer([script, script])

    class InspectingAnalyzer:
        def analyze(self, analysis_input):
            self.input = analysis_input
            self.returned = fake.analyze(analysis_input)
            return self.returned

    adapter = InspectingAnalyzer()
    result = analyze_incident(adapter)
    before = fake.analysis_inputs[0].to_json()
    object.__setattr__(adapter.input.incident_input.sources[0], 'text', 'changed')
    object.__setattr__(adapter.returned.facts[0].observation, 'value', 'changed')
    exported = fake.analysis_inputs
    object.__setattr__(exported[0].code_locations[0].citation, 'line', 1)
    payload = fake.analysis_inputs[0].to_payload()
    payload['incident_input']['sources'].clear()
    assert fake.analysis_inputs[0].to_json() == before
    assert result.to_json() == expected
    assert analyze_incident(fake).to_json() == expected


def test_sequence_and_exhaustion_count_attempts_without_reuse():
    scripts = [candidate(), candidate(True)]
    fake = FakeIncidentAnalyzer(scripts)
    assert analyze_incident(fake).to_json() == scripts[0].to_json()
    assert analyze_incident(fake).to_json() == scripts[1].to_json()
    assert fake.call_count == 2 and fake.remaining_candidates == 0
    with pytest.raises(RuntimeError, match='no scripted candidates'):
        analyze_incident(fake)
    assert fake.call_count == 3 and fake.remaining_candidates == 0


def test_empty_script_fails_without_default_success():
    fake = FakeIncidentAnalyzer([])
    with pytest.raises(RuntimeError, match='no scripted candidates'):
        analyze_incident(fake)
    assert fake.call_count == 1


def test_analyzer_exception_propagates_once():
    error = RuntimeError('analyzer failed')
    class Broken:
        calls = 0

        def analyze(self, analysis_input):
            self.calls += 1
            raise error
    analyzer = Broken()
    with pytest.raises(RuntimeError) as caught:
        analyze_incident(analyzer)
    assert caught.value is error and analyzer.calls == 1


def test_fake_rejects_wrong_constructor_and_input_types():
    with pytest.raises(TypeError):
        FakeIncidentAnalyzer([candidate(), {}])
    fake = FakeIncidentAnalyzer([candidate()])
    with pytest.raises(TypeError):
        fake.analyze({})
    assert fake.call_count == 0 and fake.remaining_candidates == 1


def test_sources_changed_during_analysis_are_revalidated(tmp_path):
    path = tmp_path / 'context.json'
    path.write_bytes(DEFAULT_CONTEXT_ARTIFACT_PATH.read_bytes())
    class ReplacingAnalyzer:
        calls = 0

        def analyze(self, analysis_input):
            self.calls += 1
            path.write_text('{}')
            return candidate()
    analyzer = ReplacingAnalyzer()
    with pytest.raises(ValueError):
        analyze_incident(analyzer, context_artifact_path=path)
    assert analyzer.calls == 1


def test_analyzer_receives_copy_of_prepared_input(monkeypatch):
    import src.examples.code_understanding_agent.incident_analyzer as module
    prepared = load_incident_analysis_input()
    before = prepared.to_json()
    monkeypatch.setattr(module, 'load_incident_analysis_input', lambda **kwargs: prepared)
    class Analyzer:
        def analyze(self, analysis_input):
            object.__setattr__(analysis_input.incident_input.sources[0], 'text', 'changed')
            return candidate()
    assert isinstance(analyze_incident(Analyzer()), IncidentAnalysisResult)
    assert prepared.to_json() == before
