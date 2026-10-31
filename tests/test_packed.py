import importlib.util
import json
import io
import zipfile
import pytest


def gate(file_id='f1', gate_id='g1'):
    return {'g8son_version': '1.0', 'file_id': file_id, 'gates': [
        {'gate_id': gate_id, 'type': 'human.review', 'conditions': ['Check source'],
         'outcomes': {'PASS': 'continue', 'FAIL': 'stop', 'INTERMEDIATE': 'review'}}]}


def unit(uid='u1'):
    from texport import packed
    state = {'gst_version': '1.0', 'state_id': uid+'.state',
             'internal': {'prior': {}, 'current': {}}, 'external': {'prior': {}, 'current': {}},
             'continuity': {'placeholder': '0,0'}, 'intent': 'Review source',
             'outcome_expectation': 'Human review', 'constraints': {}}
    return packed.create_unit(uid, state, {'gates/first.g8son': packed.dump(gate())})


def test_single_file_contains_resources_and_real_review():
    assert importlib.util.find_spec('texport.packed'), 'packed core missing'
    from texport import packed
    blob = unit()
    assert blob[:4] == b'PK\x03\x04'
    result = packed.read(blob)
    assert result['manifest']['unit_id'] == 'u1'
    assert set(result['members']) == {'container.json', 'manifest.json', 'flow.ork',
        'state/context.gst', 'gates/first.g8son', 'trace/execution.qson'}
    trace = packed.loads(result['members']['trace/execution.qson'])
    assert trace['events'][0]['event_type'] == 'review'
    assert trace['events'][0]['actor'] == {'type': 'human', 'id': 'texport-user'}
    assert 'result' not in trace['events'][0]
    assert trace['events'][0]['sequence'] == 1
    assert packed.read(blob)['manifest'] == result['manifest']
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())


def test_json_exponent_overflow_and_unlisted_gate_definitions_rejected():
    from texport import packed
    with pytest.raises(ValueError): packed.loads(b'{"value":1e999}')
    with pytest.raises(ValueError):
        packed.read(repack(unit(), lambda m: m.update({'gates/unlisted.g8son': packed.dump(gate('f2', 'g2'))})))


def test_create_unit_rejects_empty_gates_and_reserved_resource_override():
    from texport import packed
    state = packed.loads(packed.read(unit())['members']['state/context.gst'])
    for files in [{}, {'flow.ork': packed.dump(gate())}, {'gates/a.g8son': packed.dump(gate()), 'manifest.json': b'{}'}]:
        with pytest.raises(ValueError): packed.create_unit('u', state, files)


@pytest.mark.parametrize('change', ['extra_json', 'manifest_field', 'manifest_metadata', 'trace_metadata', 'event_reason', 'event_references'])
def test_canonical_schema_fields_are_checked_even_for_optional_data(change):
    from texport import packed
    def mutate(m):
        if change == 'extra_json': m['extra.json'] = b'{"a":1,"a":2}'
        elif change.startswith('manifest'):
            mf = packed.loads(m['manifest.json'])
            mf['code' if change == 'manifest_field' else 'metadata'] = 'not allowed'
            m['manifest.json'] = packed.dump(mf)
        else:
            q = packed.loads(m['trace/execution.qson'])
            if change == 'trace_metadata': q['metadata'] = []
            if change == 'event_reason': q['events'][0]['reason'] = []
            if change == 'event_references': q['events'][0]['references'] = ['a', 'a']
            m['trace/execution.qson'] = packed.dump(q)
    with pytest.raises(ValueError): packed.read(repack(unit(), mutate))


def test_copy_multi_gate_files_preserves_bytes_events_and_original():
    from texport import packed
    assert hasattr(packed, 'add_gates'), 'copy import missing'
    original = unit()
    before = packed.read(original)
    files = {'second.g8son': b'  '+packed.dump(gate('f2', 'g2')), 'third.g8son': packed.dump(gate('f3', 'g3'))}
    changed = packed.read(packed.add_gates(original, files))
    assert packed.read(original) == before
    for name, data in files.items():
        assert changed['members']['gates/'+name] == data
    for path, data in before['members'].items():
        if path not in {'container.json', 'manifest.json', 'flow.ork', 'trace/execution.qson'}:
            assert changed['members'][path] == data
    trace = packed.loads(changed['members']['trace/execution.qson'])
    assert trace['events'][:-1] == packed.loads(before['members']['trace/execution.qson'])['events']
    assert trace['events'][-1]['event_type'] == 'review'
    assert trace['events'][-1]['sequence'] == 2
    assert packed.loads(changed['members']['flow.ork'])['flow'] == ['g1', 'g2', 'g3']
    for invalid in [{}, {'first.g8son': files['second.g8son']}, {'x.g8son': packed.dump(gate())},
                    {'x.g8son': b'{}'}, {'../bad.g8son': files['second.g8son']}]:
        with pytest.raises(ValueError): packed.add_gates(original, invalid)


@pytest.mark.parametrize('count', [4, 6, 8, 12])
def test_package_contains_complete_unique_units_in_explicit_order(count):
    from texport import packed
    assert hasattr(packed, 'create_package'), 'package API missing'
    units = {f'units/u{i}.mg8': unit(f'u{i}') for i in reversed(range(count))}
    result = packed.read(packed.create_package('pkg', units))
    assert result['manifest']['units'] == list(units)
    ork = packed.loads(result['members']['system.ork'])
    assert ork == {'ork_profile': 'mg8pk-sequential/1.0', 'flow': list(units),
                   'max_steps': count, 'on_fail': 'stop', 'on_unknown': 'review'}
    for p, b in units.items(): assert result['members'][p] == b
    for invalid in [{}, {'units/a.mg8': unit(), 'units/b.mg8': unit()},
                    {f'units/{i}.mg8': unit(str(i)) for i in range(33)}]:
        with pytest.raises(ValueError): packed.create_package('p', invalid)


def repack(blob, mutate):
    from texport import packed
    members = packed.read(blob)['members'].copy()
    del members['container.json']
    mutate(members)
    return packed.archive(members)


@pytest.mark.parametrize('name', ['../bad', '/root', 'C:/bad', 'a\\\\b', 'a//b',
    'a/./b', 'a/../b', 'a/\x00b', 'a/\nb', 'é.g8son', '.hidden', 'a/'])
def test_reject_unsafe_paths(name):
    from texport import packed
    with pytest.raises(ValueError):
        repack(unit(), lambda m: m.update({name: b'{}'}))


@pytest.mark.filterwarnings('ignore:Duplicate name:UserWarning')
def test_reject_duplicate_paths_and_case_collisions():
    from texport import packed
    for names in [('a', 'a'), ('A', 'a')]:
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            for n in names:
                z.writestr(n, b'{}')
        with pytest.raises(ValueError):
            packed.read(out.getvalue())


@pytest.mark.parametrize('change', ['profile', 'hash', 'missing', 'json', 'infinity', 'flow', 'gate', 'trace', 'state', 'reference'])
def test_reject_invalid_container_or_resource(change):
    from texport import packed
    members = packed.read(unit())['members'].copy()
    if change == 'profile':
        envelope = packed.loads(members['container.json']); envelope['profile'] = 'unknown/1'
        members['container.json'] = packed.dump(envelope)
    elif change == 'hash':
        members['flow.ork'] += b' '
    else:
        del members['container.json']
        if change == 'missing': del members['gates/first.g8son']
        if change == 'json': members['manifest.json'] = b'{"unit_id":"a","unit_id":"b"}'
        if change == 'infinity': members['state/context.gst'] = b'{"x":NaN}'
        if change == 'flow': members['flow.ork'] = packed.dump({'ork_profile': 'unknown', 'flow': ['g1']})
        if change == 'gate': members['gates/first.g8son'] = packed.dump({'g8son_version': '1.0', 'file_id': 'x', 'gates': []})
        if change == 'trace': members['trace/execution.qson'] = packed.dump({'qson_version': '1.0', 'run_id': 'r', 'events': []})
        if change == 'state': members['state/context.gst'] = packed.dump({'gst_version': '1.0'})
        if change == 'reference':
            mf = packed.loads(members['manifest.json']); mf['entry'] = '../bad.ork'
            members['manifest.json'] = packed.dump(mf)
        blob = packed.archive(members)
        with pytest.raises(ValueError): packed.read(blob)
        return
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for n, b in members.items(): z.writestr(n, b)
    with pytest.raises(ValueError): packed.read(out.getvalue())


def test_zip_headers_crc_methods_and_bounds():
    from texport import packed
    blob = unit()
    for altered in [blob[:-10], b'x'* (16*1024*1024+1), blob[:8]+b'\x08\x00'+blob[10:]]:
        with pytest.raises(ValueError): packed.read(altered)
    for compression, mode in [(zipfile.ZIP_DEFLATED, 0o100644), (zipfile.ZIP_STORED, 0o120777)]:
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            info = zipfile.ZipInfo('link'); info.compress_type = compression; info.external_attr = mode << 16
            z.writestr(info, b'bad')
        with pytest.raises(ValueError): packed.read(out.getvalue())
    with pytest.raises(ValueError): repack(blob, lambda m: m.update({'huge.txt': b'x'*(1024*1024+1)}))
