"""Regression boundaries from independent packed-authoring review."""
import pytest
from test_packed import unit, gate
from texport import packed


@pytest.mark.parametrize('field,value', [('execute', {}), ('metadata', []), ('package_id', 'x'*201)])
def test_package_manifest_boundary(field, value):
    blob = packed.create_package('p', {'units/a.mg8': unit()})
    blob = edit(blob, 'manifest.json', lambda f: f.update({field: value}), 'package')
    with pytest.raises(ValueError): packed.read(blob)


@pytest.mark.parametrize('offset,value', [(34, 1), (38, 0x10)])
def test_central_split_volume_and_dos_directory_rejected(offset, value):
    blob = bytearray(unit()); start = blob.index(b'PK\x01\x02')
    blob[start+offset] |= value
    with pytest.raises(ValueError): packed.read(bytes(blob))


@pytest.mark.parametrize('kind', ['path', 'unit_id', 'state_id', 'file_id', 'gate_id', 'run_id', 'trace_id'])
def test_shared_path_and_identity_caps(kind):
    if kind == 'path':
        packed.safe_path('a'*240)
        with pytest.raises(ValueError): packed.safe_path('a'*241)
        return
    if kind == 'unit_id':
        blob = edit(unit(), 'manifest.json', lambda o: o.update(unit_id='x'*201))
    elif kind == 'state_id':
        blob = edit(unit(), 'state/context.gst', lambda o: o.update(state_id='x'*201))
    elif kind in ('file_id', 'gate_id'):
        def mutate(o):
            if kind == 'file_id': o['file_id'] = 'x'*201
            else: o['gates'][0]['gate_id'] = 'x'*201
        obj = gate(); mutate(obj)
        with pytest.raises(ValueError): packed.validate_gates(packed.dump(obj))
        return
    else:
        def mutate(o):
            if kind == 'run_id':
                o['run_id'] = o['events'][0]['run_id'] = 'x'*201
            else: o['events'][0]['trace_id'] = 'x'*201
        blob = edit(unit(), 'trace/execution.qson', mutate)
    with pytest.raises(ValueError): packed.read(blob)


@pytest.mark.parametrize('condition', ['', '   ', '\t\n'])
def test_blank_conditions_rejected(condition):
    obj = gate(); obj['gates'][0]['conditions'] = [condition]
    with pytest.raises(ValueError): packed.validate_gates(packed.dump(obj))


@pytest.mark.parametrize('timestamp', ['not-a-date', '2026-01-01T12:00:00', '2026-02-30T12:00:00Z', '2026-01-01 12:00:00Z', '2026-01-01T12:00:00+25:00'])
def test_qson_requires_real_timezone_timestamp(timestamp):
    blob = edit(unit(), 'trace/execution.qson', lambda o: o['events'][0].update(timestamp=timestamp))
    with pytest.raises(ValueError): packed.read(blob)


@pytest.mark.parametrize('timestamp', ['2026-01-01T12:00:00Z', '2026-01-01t12:00:00.123z', '2026-01-01T12:00:00-04:00'])
def test_qson_accepts_rfc3339(timestamp):
    packed.read(edit(unit(), 'trace/execution.qson', lambda o: o['events'][0].update(timestamp=timestamp)))


@pytest.mark.parametrize('change', ['run', 'gap', 'bool'])
def test_qson_run_binding_and_contiguous_sequence(change):
    def mutate(o):
        if change == 'run': o['events'][0]['run_id'] = 'other'
        else: o['events'][0]['sequence'] = 2 if change == 'gap' else True
    with pytest.raises(ValueError): packed.read(edit(unit(), 'trace/execution.qson', mutate))


def test_archive_writer_bounds_include_zip_overhead(monkeypatch):
    members = {'payload.txt': b'x'*100}
    blob = packed.archive(members)
    monkeypatch.setattr(packed, 'LIMIT', len(blob)-1)
    with pytest.raises(ValueError): packed.archive(members)


@pytest.mark.parametrize('cap', ['LIMIT', 'MEMBER_LIMIT'])
def test_workspace_writer_shared_byte_caps(monkeypatch, cap):
    from texport import icm
    from test_icm import SESSIONS
    import copy
    sessions = copy.deepcopy(SESSIONS)
    sessions[0]['lines'][0]['text'] = 'x'*4096
    monkeypatch.setattr(packed, cap, 2048)
    plan = icm.validate_plan(sessions, icm.direct_plan(sessions))
    with pytest.raises(ValueError): icm.workspace(sessions, plan, approved=True)


def test_permuted_reviewed_flow_survives_append():
    blob = packed.add_gates(unit(), {'b.g8son': packed.dump(gate('f2', 'g2'))})
    blob = edit(blob, 'flow.ork', lambda o: o['flow'].reverse())
    changed = packed.read(packed.add_gates(blob, {'c.g8son': packed.dump(gate('f3', 'g3'))}))
    assert packed.loads(changed['members']['flow.ork'])['flow'] == ['g2', 'g1', 'g3']


def edit(blob, path, change, kind='unit'):
    members = packed.read(blob)['members'].copy()
    del members['container.json']
    obj = packed.loads(members[path]); change(obj)
    members[path] = packed.dump(obj)
    return packed.archive(members, kind)


@pytest.mark.parametrize('kind,extra', [('unit', 'branches'), ('unit', 'parallel'), ('package', 'parallel')])
def test_ork_rejects_undeclared_execution_fields(kind, extra):
    blob = unit() if kind == 'unit' else packed.create_package('p', {'units/a.mg8': unit()})
    path = 'flow.ork' if kind == 'unit' else 'system.ork'
    blob = edit(blob, path, lambda f: f.update({extra: []}), kind)
    with pytest.raises(ValueError): packed.read(blob)
    if kind == 'unit':
        with pytest.raises(ValueError): packed.add_gates(blob, {'b.g8son': packed.dump(gate('f2', 'g2'))})
