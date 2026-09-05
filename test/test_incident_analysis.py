import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from evaluation.replay_incident_context import DEFAULT_FIXTURE_PATH, DEFAULT_SEARCH_ARTIFACT_PATH
from src.examples.code_understanding_agent.incident_analysis import (
    IncidentAnalysisCandidate, validate_incident_analysis,
)


def payload():
    return {
        'case_id': 'incident-click-unexpected-extra-argument-001',
        'facts': [
            {'kind': 'incident', 'fact_id': 'f1', 'observation': {
                'value': 'Got unexpected extra argument', 'source_ids': ['log-001']}},
            {'kind': 'code', 'fact_id': 'f2', 'citation': {
                'repo': 'click', 'path': 'src/click/core.py', 'line': 1284},
             'snippet': '"Got unexpected extra argument ({args})",'},
        ],
        'hypotheses': [{'hypothesis_id': 'h1', 'statement': 'The invocation may contain an extra argument.',
                        'supporting_fact_ids': ['f1', 'f2'], 'confidence': 'low'}],
        'missing_information': [{'information_id': 'm1', 'question': 'What exact arguments and command declaration were used?'}],
        'investigation_steps': [{'step_id': 's1', 'action': 'Compare supplied arguments with the command declaration.',
                                 'related_hypothesis_ids': ['h1']}],
    }


def validate(value, **kwargs):
    return validate_incident_analysis(json.dumps(value), **kwargs)


def test_roundtrip_and_insufficient_evidence():
    result = validate(payload())
    assert validate_incident_analysis(result.to_json()).to_json() == result.to_json()
    value = payload()
    value['hypotheses'] = []
    value['investigation_steps'][0]['related_hypothesis_ids'] = []
    assert validate(value).hypotheses == ()
    value['facts'] = []
    assert validate(value).facts == ()


MUTATIONS = [
    ('case_id', 'other-case'),
    ('facts.0.observation.source_ids', ['unknown']),
    ('facts.0.observation.source_ids', ['log-001', 'unknown']),
    ('facts.0.observation.source_ids', ['log-001', 'log-001']),
    ('facts.0.observation.value', 'got unexpected extra argument'),
    ('facts.0.observation.value', 'The root cause is invalid configuration'),
    ('facts.1.citation.repo', 'other'),
    ('facts.1.citation.path', '/src/click/core.py'),
    ('facts.1.citation.path', 'src/click/other.py'),
    ('facts.1.citation.line', 1285),
    ('facts.1.citation.line', '1284'),
    ('facts.1.citation.line', True),
    ('facts.1.citation.line', 1284.0),
    ('facts.1.citation.line', '1284-1285'),
    ('facts.1.snippet', 'fabricated'),
    ('facts.1.fact_id', 'f1'),
    ('facts.0.fact_id', ' '),
    ('hypotheses.0.supporting_fact_ids', []),
    ('hypotheses.0.supporting_fact_ids', ['unknown']),
    ('hypotheses.0.supporting_fact_ids', ['f1', 'f1']),
    ('hypotheses.0.confidence', 'certain'),
    ('hypotheses.0.confidence', 1),
    ('hypotheses.0.confidence', True),
    ('hypotheses.0.statement', ' '),
    ('missing_information.0.question', ''),
    ('investigation_steps.0.action', ' '),
    ('investigation_steps.0.related_hypothesis_ids', ['unknown']),
    ('investigation_steps.0.related_hypothesis_ids', ['h1', 'h1']),
    ('facts', []),
]


def mutate(value, path, replacement):
    parts = path.split('.')
    current = value
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    current[parts[-1]] = replacement


@pytest.mark.parametrize('path,replacement', MUTATIONS)
def test_reject_mutations(path, replacement):
    value = payload()
    mutate(value, path, replacement)
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize('section', ['facts', 'hypotheses', 'missing_information', 'investigation_steps'])
def test_duplicate_ids(section):
    value = payload()
    value[section].append(deepcopy(value[section][0]))
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize('section', [None, 'facts', 'hypotheses', 'missing_information', 'investigation_steps'])
@pytest.mark.parametrize('operation', ['extra', 'missing'])
def test_exact_fields(section, operation):
    value = payload()
    target = value if section is None else value[section][0]
    if operation == 'extra':
        target['answer'] = 'A claimed diagnosis'
    else:
        del target[next(iter(target))]
    with pytest.raises(ValueError):
        validate(value)


def test_empty_hypotheses_need_question_and_no_flat_answer():
    value = payload()
    value.update(hypotheses=[], missing_information=[], investigation_steps=[])
    with pytest.raises(ValueError):
        validate(value)
    with pytest.raises(ValueError):
        validate({'answer': 'An extra argument caused the error'})


def test_detached_and_revalidates_constructed_model():
    candidate = IncidentAnalysisCandidate.model_validate_json(json.dumps(payload()))
    result = validate_incident_analysis(candidate)
    saved = result.to_json()
    exported = result.to_payload()
    exported['facts'][0]['observation']['source_ids'].append('bad')
    object.__setattr__(candidate.facts[0].observation, 'value', 'fabricated')
    assert result.to_json() == saved
    with pytest.raises(ValueError):
        validate_incident_analysis(candidate)
    with pytest.raises(ValidationError):
        result.case_id = 'other'


@pytest.mark.parametrize('filename', ['fixture', 'search', 'context'])
@pytest.mark.parametrize('drift', ['value', 'bool', 'float', 'extra'])
def test_upstream_revalidation(tmp_path, filename, drift):
    from src.examples.code_understanding_agent.incident_analysis import DEFAULT_CONTEXT_ARTIFACT_PATH
    source = {'fixture': DEFAULT_FIXTURE_PATH, 'search': DEFAULT_SEARCH_ARTIFACT_PATH,
              'context': DEFAULT_CONTEXT_ARTIFACT_PATH}[filename]
    value = json.loads(source.read_text())
    if filename == 'fixture':
        value['schema_version'] = {'value': 9, 'bool': True, 'float': 1.0, 'extra': 1}[drift]
    elif filename == 'search':
        value['replay']['calls']['search_code'] = {'value': 9, 'bool': True, 'float': 1.0, 'extra': 1}[drift]
    else:
        value['replay']['calls']['get_file_context'] = {'value': 9, 'bool': True, 'float': 1.0, 'extra': 1}[drift]
    if drift == 'extra':
        value['unexpected'] = True
    path = tmp_path / source.name
    path.write_text(json.dumps(value))
    if filename == 'fixture':
        path.with_suffix('.json.sha256').write_text(source.with_suffix('.json.sha256').read_text())
    with pytest.raises(ValueError):
        validate(payload(), **{filename + '_path' if filename == 'fixture' else filename + '_artifact_path': path})


@pytest.fixture(autouse=True)
def prohibit_execution(monkeypatch):
    import socket
    from src.examples.code_understanding_agent.agent_loop import AgentLoop
    from src.examples.code_understanding_agent.model_boundary import FakeModel
    from src.examples.code_understanding_agent.tool_router import ToolRouter

    def forbidden(*args, **kwargs):
        pytest.fail('offline analysis attempted execution')

    monkeypatch.setattr(ToolRouter, 'execute', forbidden)
    monkeypatch.setattr(AgentLoop, 'run', forbidden)
    monkeypatch.setattr(AgentLoop, 'resume', forbidden)
    monkeypatch.setattr(FakeModel, 'decide', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def test_mapping_input_and_step_order_are_detached():
    value = payload()
    value['investigation_steps'].append({
        'step_id': 's0', 'action': 'Request the installed Click version.',
        'related_hypothesis_ids': [],
    })
    candidate = IncidentAnalysisCandidate.model_validate_json(json.dumps(value))
    raw = candidate.model_dump(mode='python')
    result = validate_incident_analysis(raw)
    saved = result.to_json()
    raw['facts'][0]['observation']['value'] = 'changed'
    assert result.to_json() == saved
    assert [s.step_id for s in result.investigation_steps] == ['s1', 's0']
    assert validate_incident_analysis(saved).to_json() == saved


@pytest.mark.parametrize('field', ['repo', 'path', 'line'])
def test_constructed_nested_citation_is_revalidated(field):
    candidate = IncidentAnalysisCandidate.model_validate_json(json.dumps(payload()))
    object.__setattr__(candidate.facts[1].citation, field, True)
    with pytest.raises(ValueError):
        validate_incident_analysis(candidate)


def test_reloading_sources_does_not_mutate_saved_result(tmp_path):
    from src.examples.code_understanding_agent.incident_analysis import DEFAULT_CONTEXT_ARTIFACT_PATH
    path = tmp_path / 'context.json'
    path.write_bytes(DEFAULT_CONTEXT_ARTIFACT_PATH.read_bytes())
    result = validate(payload(), context_artifact_path=path)
    saved = result.to_json()
    path.write_text('{}')
    assert result.to_json() == saved
    with pytest.raises(ValueError):
        validate(payload(), context_artifact_path=path)
