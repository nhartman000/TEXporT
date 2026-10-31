import copy
import importlib.util
import io
import zipfile
import pytest

SESSIONS = [{'id': 'capture-1', 'title': 'Original', 'source': 'screen', 'captured_at': '2026-10-31',
 'lines': [{'text': 'Keep EXACT 42'}, {'text': 'Full surrounding context'}, {'text': 'Check budget'}],
 'markers': [{'start': 0, 'end': 0, 'note': 'precise'}, {'start': 2, 'end': 2, 'note': 'g8son: review budget'}]}]


def test_direct_workspace_keeps_markers_context_notes_and_no_invented_gates():
    assert importlib.util.find_spec('texport.icm'), 'ICM missing'
    from texport import icm, packed
    plan = icm.direct_plan(SESSIONS, 'Work')
    assert plan['groups'][0]['marker_ids'] == ['M1', 'M2']
    assert plan['groups'][0]['conditions'] == []
    blob = icm.workspace(SESSIONS, plan, approved=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert {'README.md', 'AGENTS.md', 'CONTEXT.md', 'sources/compiled-markers.txt',
                'sources/snapshot.json', 'state/global.gst', 'organization.json'} <= set(z.namelist())
        assert packed.loads(z.read('sources/snapshot.json')) == SESSIONS
        compiled = z.read('sources/compiled-markers.txt').decode()
        assert 'Keep EXACT 42' in compiled and 'Full surrounding context' in compiled and 'precise' in compiled
        assert not any(n.endswith('.mg8') for n in z.namelist())
        assert len([n for n in z.namelist() if n.startswith('themes/') and n.endswith('.txt')]) == 2
        state = packed.loads(z.read('state/global.gst'))
        assert state['internal']['current']['global']['gestalt'] is None
        assert state['internal']['current']['markers'][0]['text'] == 'Keep EXACT 42'
    with pytest.raises(ValueError): icm.workspace(SESSIONS, plan, approved=False)


def test_ai_prompt_and_reviewed_conditions_split_into_strict_bounded_files():
    from texport import icm, packed
    assert hasattr(icm, 'proposal_prompt'), 'AI two-step prompt missing'
    prompt = icm.proposal_prompt(SESSIONS, 'Work')
    assert 'untrusted' in prompt.lower() and 'M1' in prompt and 'Keep EXACT 42' in prompt
    plan = icm.direct_plan(SESSIONS, 'Work')
    plan['gestalt'] = 'Budget review'
    plan['variables'] = {'budget': None, 'limit': 42}
    plan['groups'][0]['conditions'] = [{'text': f'Budget condition {i}', 'marker_ids': ['M2']} for i in range(7)]
    approved = icm.validate_plan(SESSIONS, packed.dump(plan))
    blob = icm.workspace(SESSIONS, approved, approved=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        paths = [p for p in z.namelist() if p.endswith('.mg8')]
        assert len(paths) == 1
        unit = packed.read(z.read(paths[0]))
        assert len(unit['manifest']['gates']) == 3
        gates = [g for p in unit['manifest']['gates'] for g in packed.loads(unit['members'][p])['gates']]
        assert len(gates) == 7 and gates[0]['metadata']['source_marker_ids'] == ['M2']
        state = packed.loads(unit['members']['state/context.gst'])
        assert state['internal']['current']['global']['variables']['budget'] is None
        assert state['external']['current']['sessions'] == SESSIONS
        for p in unit['manifest']['gates']:
            assert z.read('themes/uncategorized/'+p) == unit['members'][p]


@pytest.mark.parametrize('bad', ['unknown', 'missing', 'duplicate', 'unsafe', 'extra', 'condition_source', 'condition_code', 'variables', 'empty', 'oversize'])
def test_invalid_proposals_never_export(bad):
    from texport import icm
    plan = icm.direct_plan(SESSIONS, 'Work')
    if bad == 'unknown': plan['groups'][0]['marker_ids'] = ['M99']
    if bad == 'missing': plan['groups'][0]['marker_ids'] = ['M1']
    if bad == 'duplicate': plan['groups'].append(copy.deepcopy(plan['groups'][0]))
    if bad == 'unsafe': plan['groups'][0]['name'] = '../escape'
    if bad == 'extra': plan['execute'] = 'shell'
    if bad == 'condition_source': plan['groups'][0]['conditions'] = [{'text': 'Check', 'marker_ids': ['M99']}]
    if bad == 'condition_code': plan['groups'][0]['conditions'] = [{'text': 'Check', 'marker_ids': ['M1'], 'code': 'eval'}]
    if bad == 'variables': plan['variables'] = {'nested': {'path': 'x'}}
    if bad == 'empty': plan['groups'] = []
    if bad == 'oversize': plan['title'] = 'x'*5000
    with pytest.raises(ValueError): icm.workspace(SESSIONS, plan, approved=True)
