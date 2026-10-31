"""Offline packed authoring containers. No evaluator, network or filesystem extraction."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import stat
import struct
import uuid
import zipfile
from datetime import datetime, timezone

UNIT_PROFILE = 'mg8-packed/1.0'
PACKAGE_PROFILE = 'mg8pk-packed/1.0'
UNIT_ORK = 'texport-packed-authoring/1.0'
PACKAGE_ORK = 'mg8pk-sequential/1.0'


def dump(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode('utf-8')


LIMIT = 16 * 1024 * 1024
MEMBER_LIMIT = 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe_path(name):
    require(isinstance(name, str) and len(name) <= 240 and bool(re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_./-]*', name))
            and all(p not in ('', '.', '..') for p in name.split('/')), 'Unsafe resource path')
    return name


def text(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 4096


def identity(value):
    return text(value) and len(value) <= 200 and not re.search(r'[\x00-\x1f\x7f-\x9f]', value)


def loads(data):
    def pairs(items):
        out = {}
        for k, v in items:
            require(k not in out, 'Duplicate JSON key')
            out[k] = v
        return out
    def invalid(value):
        raise ValueError('Non-finite JSON')
    def finite(value):
        number = float(value)
        require(math.isfinite(number), 'Non-finite JSON number')
        return number
    try:
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite)
    except (UnicodeError, RecursionError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid UTF-8 JSON') from exc


def validate_gates(data):
    require(isinstance(data, bytes) and len(data) <= MEMBER_LIMIT, 'Gate file exceeds limit')
    obj = loads(data)
    require(isinstance(obj, dict) and set(obj) <= {'g8son_version', 'file_id', 'gates', 'metadata'}, 'Invalid G8SON fields')
    require(obj.get('g8son_version') == '1.0' and identity(obj.get('file_id')), 'Invalid G8SON identity/version')
    gates = obj.get('gates')
    require(isinstance(gates, list) and 1 <= len(gates) <= 3, 'G8SON needs 1..3 gates')
    ids = set()
    for gate in gates:
        require(isinstance(gate, dict) and identity(gate.get('gate_id')) and text(gate.get('type')), 'Invalid gate identity/type')
        require(gate['gate_id'] not in ids, 'Duplicate gate identity')
        ids.add(gate['gate_id'])
        require('order' not in gate or type(gate['order']) is int and gate['order'] >= 1, 'Invalid gate order')
        conditions = gate.get('conditions')
        require(isinstance(conditions, list) and 1 <= len(conditions) <= 128 and
                all(isinstance(c, dict) or isinstance(c, str) and bool(c.strip()) for c in conditions), 'Invalid gate conditions')
        require('metadata' not in gate or isinstance(gate['metadata'], dict), 'Invalid gate metadata')
        if 'outcomes' in gate:
            outcomes = gate['outcomes']
            require(isinstance(outcomes, dict) and set(outcomes) <= {'PASS', 'FAIL', 'INTERMEDIATE'} and
                    all(v is None or isinstance(v, (str, dict)) for v in outcomes.values()), 'Invalid outcomes')
    require('metadata' not in obj or isinstance(obj['metadata'], dict), 'Invalid G8SON metadata')
    return obj


def validate_state(obj):
    require(isinstance(obj, dict) and obj.get('gst_version') == '1.0' and identity(obj.get('state_id')), 'Invalid GST')
    for name in ('internal', 'external'):
        require(isinstance(obj.get(name), dict) and {'prior', 'current'} <= set(obj[name]), 'Missing GST state snapshots')
    require(isinstance(obj.get('continuity'), dict) and text(obj.get('intent')) and
            text(obj.get('outcome_expectation')) and isinstance(obj.get('constraints'), dict), 'Incomplete GST context')


def validate_trace(obj):
    require(isinstance(obj, dict) and obj.get('qson_version') == '1.0' and identity(obj.get('run_id')), 'Invalid QSON')
    require(set(obj) <= {'qson_version', 'run_id', 'events', 'metadata'}, 'Invalid QSON fields')
    require('metadata' not in obj or isinstance(obj['metadata'], dict), 'Invalid QSON metadata')
    events = obj.get('events')
    require(isinstance(events, list) and 1 <= len(events) <= 10000, 'QSON requires real events')
    seen = set()
    for i, ev in enumerate(events, 1):
        require(isinstance(ev, dict) and identity(ev.get('trace_id')) and ev['trace_id'] not in seen and
                ev.get('run_id') == obj['run_id'] and type(ev.get('sequence')) is int and ev['sequence'] == i,
                'Invalid event identity/sequence')
        seen.add(ev['trace_id'])
        require(ev.get('event_type') in {'gate_attempt', 'override', 'correction', 'state_commit',
                'external_input', 'runtime_error', 'review'}, 'Invalid event type')
        actor = ev.get('actor')
        require(isinstance(actor, dict) and identity(actor.get('type')) and identity(actor.get('id')), 'Invalid actor')
        require('result' not in ev or ev['result'] in {'PASS', 'FAIL', 'INTERMEDIATE'}, 'Invalid event result')
        for field in ('file_id', 'gate_id', 'input_state_id', 'timestamp', 'reason', 'previous_event_hash', 'event_hash'):
            require(field not in ev or isinstance(ev[field], str), 'Invalid event '+field)
        for field in ('file_id', 'gate_id', 'input_state_id'):
            require(field not in ev or identity(ev[field]), 'Invalid event identity '+field)
        if 'timestamp' in ev:
            stamp = ev['timestamp']
            require(bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)', stamp)),
                    'Invalid RFC3339 timestamp')
            try:
                datetime.fromisoformat(stamp.upper().replace('Z', '+00:00'))
            except ValueError as exc:
                raise ValueError('Invalid RFC3339 timestamp') from exc
        for field in ('action', 'operation'):
            require(field not in ev or ev[field] is None or isinstance(ev[field], (str, dict)), 'Invalid event '+field)
        require('output_state_id' not in ev or ev['output_state_id'] is None or identity(ev['output_state_id']), 'Invalid output state ID')
        require('metadata' not in ev or isinstance(ev['metadata'], dict), 'Invalid event metadata')
        if 'references' in ev:
            refs = ev['references']
            require(isinstance(refs, list) and all(isinstance(r, str) and r for r in refs) and
                    len(refs) == len(set(refs)), 'Invalid event references')


def review_event(run_id, sequence, reason):
    return {'trace_id': 'TRJ_'+uuid.uuid4().hex, 'run_id': run_id, 'sequence': sequence,
            'event_type': 'review', 'actor': {'type': 'human', 'id': 'texport-user'},
            'timestamp': datetime.now(timezone.utc).isoformat(), 'reason': reason}


def archive(members, kind='unit'):
    members = dict(members)
    require('container.json' not in members, 'Envelope is generated, not supplied')
    names = set()
    for name in members:
        safe_path(name)
        require(name.casefold() not in names, 'Case-colliding members')
        names.add(name.casefold())
    members['container.json'] = dump({'profile': UNIT_PROFILE if kind == 'unit' else PACKAGE_PROFILE,
        'kind': kind, 'manifest': 'manifest.json',
        'integrity': {p: hashlib.sha256(b).hexdigest() for p, b in members.items()}})
    require(1 <= len(members) <= 128 and sum(len(b) for b in members.values()) <= LIMIT, 'Archive bounds')
    require(all(len(b) <= (LIMIT if kind == 'package' and p.startswith('units/') and p.endswith('.mg8')
                else MEMBER_LIMIT) for p, b in members.items()), 'Member limit')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as z:
        for name in sorted(members):
            item = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            item.external_attr = 0o100644 << 16
            z.writestr(item, members[name])
    result = out.getvalue()
    require(len(result) <= LIMIT, 'Archive byte length limit')
    return result


def create_unit(unit_id, state, gate_files, *, metadata=None):
    require(isinstance(gate_files, dict) and 1 <= len(gate_files) <= 120, 'Expected bounded gate files')
    flow = []
    for path, data in gate_files.items():
        safe_path(path)
        require(path.endswith('.g8son'), 'Expected .g8son resource')
        flow.extend(g['gate_id'] for g in validate_gates(data)['gates'])
    run = 'RUN_'+uuid.uuid4().hex
    manifest = {'mg8_version': '1.0', 'unit_id': unit_id, 'entry': 'flow.ork',
                'state': ['state/context.gst'], 'gates': list(gate_files),
                'trace': 'trace/execution.qson', 'metadata': metadata or {}}
    members = {**gate_files, 'manifest.json': dump(manifest), 'state/context.gst': dump(state),
        'flow.ork': dump({'ork_profile': UNIT_ORK, 'flow': flow, 'max_iterations': 1,
                          'on_fail': 'stop', 'on_unknown': 'review'}),
        'trace/execution.qson': dump({'qson_version': '1.0', 'run_id': run,
             'events': [review_event(run, 1, 'Assembled/exported reviewed authoring unit; gates unevaluated.')]})}
    blob = archive(members)
    read(blob)
    return blob


def add_gates(blob, files):
    """Copy ordered {basename: bytes} onto a unit. Returns NEW bytes; never writes sources.

    Appends gates in supplied insertion order. Caller must review/approve before saving.
    """
    source = read(blob)
    require(source['container']['kind'] == 'unit' and isinstance(files, dict) and files, 'Drop gate files onto a unit')
    members, manifest = source['members'].copy(), source['manifest']
    flow = loads(members[manifest['entry']])
    for name, data in files.items():
        safe_path(name)
        require('/' not in name and name.endswith('.g8son'), 'Expected .g8son basename')
        path = 'gates/'+name
        require(path.casefold() not in {p.casefold() for p in members}, 'Destination collision')
        obj = validate_gates(data)
        members[path] = data
        manifest['gates'].append(path)
        flow['flow'].extend(g['gate_id'] for g in obj['gates'])
    trace = loads(members[manifest['trace']])
    trace['events'].append(review_event(trace['run_id'], len(trace['events'])+1,
        'Reviewed copied gate import; appended flow order; no evaluation: '+', '.join(files)))
    members['manifest.json'] = dump(manifest)
    members[manifest['entry']] = dump(flow)
    members[manifest['trace']] = dump(trace)
    del members['container.json']
    result = archive(members)
    read(result)
    return result


def create_package(package_id, units, *, metadata=None):
    """Pack an insertion-ordered {units/name.mg8: complete unit bytes} mapping."""
    require(isinstance(units, dict) and 1 <= len(units) <= 32 and text(package_id), 'Package needs 1..32 units and identity')
    manifest = {'mg8pk_version': '1.0', 'package_id': package_id, 'entry': 'system.ork',
                'units': list(units), 'metadata': metadata or {}}
    members = {**units, 'manifest.json': dump(manifest), 'system.ork': dump({
        'ork_profile': PACKAGE_ORK, 'flow': list(units), 'max_steps': len(units),
        'on_fail': 'stop', 'on_unknown': 'review'})}
    result = archive(members, 'package')
    read(result)
    return result


def _validate_package(manifest, members):
    require(set(manifest) <= {'mg8pk_version', 'package_id', 'entry', 'units', 'metadata'} and
            ('metadata' not in manifest or isinstance(manifest['metadata'], dict)), 'Invalid package manifest fields')
    require(manifest.get('mg8pk_version') == '1.0' and identity(manifest.get('package_id')), 'Invalid package identity/version')
    entry = _resource(manifest, 'entry', '.ork', members)[0]
    paths = _resource(manifest, 'units', '.mg8', members)
    require(1 <= len(paths) <= 32, 'Package unit count')
    # Fail nested packages before recursively validating, so depth is bounded to one.
    ids = set()
    for path in paths:
        require(path.startswith('units/') and path.count('/') == 1, 'Invalid package unit path')
        nested = _zip_members(members[path])
        require('container.json' in nested and loads(nested['container.json']).get('kind') == 'unit', 'Nested package unsupported')
        unit = read(members[path])
        unit_id = unit['manifest']['unit_id']
        require(unit_id not in ids, 'Duplicate unit identity')
        ids.add(unit_id)
    flow = loads(members[entry])
    require(isinstance(flow, dict) and set(flow) == {
            'ork_profile', 'flow', 'max_steps', 'on_fail', 'on_unknown'} and flow.get('ork_profile') == PACKAGE_ORK and flow.get('flow') == paths and
            type(flow.get('max_steps')) is int and flow['max_steps'] == len(paths) and
            flow.get('on_fail') == 'stop' and flow.get('on_unknown') == 'review', 'Unsupported or mismatched package chain')
    require({p for p in members if p.endswith('.mg8')} == set(paths), 'Unlisted package units')


def _zip_members(blob):
    require(isinstance(blob, bytes) and 22 <= len(blob) <= LIMIT, 'Archive size limit')
    require(blob[-22:-18] == b'PK\x05\x06', 'Missing bounded ZIP end record')
    disk, start_disk, n_disk, count, cd_size, cd_offset, comment = struct.unpack_from('<4H2IH', blob, len(blob)-18)
    require(disk == start_disk == comment == 0 and n_disk == count and 1 <= count <= 128 and
            cd_offset + cd_size == len(blob)-22, 'Unsupported ZIP envelope')
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            infos = z.infolist()
            require(len(infos) == count, 'ZIP count mismatch')
            seen, total, pos = set(), 0, 0
            for info in infos:
                safe_path(info.orig_filename)
                require(info.filename == info.orig_filename and info.filename.casefold() not in seen, 'Duplicate ZIP identity')
                seen.add(info.filename.casefold())
                require(info.compress_type == 0 and info.compress_size == info.file_size and
                        info.flag_bits & ~0x800 == 0 and info.extract_version <= 20 and not info.extra,
                        'Unsupported ZIP method/flags/extensions')
                require(info.volume == 0, 'Split ZIP volume unsupported')
                require(not info.external_attr & 0x10, 'ZIP directory unsupported')
                mode = info.external_attr >> 16
                require(stat.S_IFMT(mode) in (0, stat.S_IFREG), 'Special ZIP member')
                require(info.header_offset == pos and pos + 30 <= cd_offset, 'Overlapping or inconsistent ZIP records')
                sig, version, flags, method, time, date, crc, csize, size, nlen, elen = struct.unpack_from('<4s5H3I2H', blob, pos)
                require(sig == b'PK\x03\x04' and flags == info.flag_bits and method == 0 and version <= 20 and
                        crc == info.CRC and csize == size == info.file_size and elen == 0, 'Local/central ZIP header mismatch')
                require(blob[pos+30:pos+30+nlen] == info.filename.encode('ascii'), 'Local ZIP path mismatch')
                pos += 30 + nlen + elen + size
                require(pos <= cd_offset and size <= LIMIT, 'ZIP member bounds')
                total += size
                require(total <= LIMIT, 'ZIP total bounds')
            require(pos == cd_offset, 'Unexpected ZIP payload')
            return {i.filename: z.read(i) for i in infos}
    except (zipfile.BadZipFile, NotImplementedError, UnicodeError, struct.error, RuntimeError) as exc:
        raise ValueError('Malformed ZIP') from exc


def _resource(manifest, key, suffix, members):
    paths = manifest.get(key)
    if key in ('entry', 'trace'):
        paths = [paths]
    require(isinstance(paths, list) and paths and all(isinstance(p, str) for p in paths)
            and len(paths) == len(set(paths)), 'Invalid resource references')
    for p in paths:
        safe_path(p)
        require(p in members and p.endswith(suffix), 'Missing or wrong resource type')
    return paths


def _validate_unit(manifest, members):
    require(set(manifest) <= {'mg8_version', 'unit_id', 'entry', 'state', 'gates', 'trace', 'metadata'} and
            ('metadata' not in manifest or isinstance(manifest['metadata'], dict)), 'Invalid canonical manifest fields')
    require(manifest.get('mg8_version') == '1.0' and identity(manifest.get('unit_id')), 'Invalid MG8 identity/version')
    entry = _resource(manifest, 'entry', '.ork', members)[0]
    states = _resource(manifest, 'state', '.gst', members)
    gates = _resource(manifest, 'gates', '.g8son', members)
    require({p for p in members if p.endswith('.g8son')} == set(gates), 'Unlisted gate definitions')
    require(not any(p.endswith(('.mg8', '.mg8pk')) for p in members), 'Nested archives unsupported in unit')
    trace = _resource(manifest, 'trace', '.qson', members)[0]
    for path in states:
        validate_state(loads(members[path]))
    file_ids, gate_ids = set(), []
    for path in gates:
        obj = validate_gates(members[path])
        require(obj['file_id'] not in file_ids, 'Duplicate file identity')
        file_ids.add(obj['file_id'])
        gate_ids.extend(g['gate_id'] for g in obj['gates'])
    require(len(gate_ids) == len(set(gate_ids)), 'Duplicate gate identity')
    flow = loads(members[entry])
    require(isinstance(flow, dict) and set(flow) == {
            'ork_profile', 'flow', 'max_iterations', 'on_fail', 'on_unknown'} and flow.get('ork_profile') == UNIT_ORK and
            isinstance(flow.get('flow'), list) and all(isinstance(g, str) for g in flow['flow']) and
            len(flow['flow']) == len(gate_ids) and set(flow['flow']) == set(gate_ids) and
            type(flow.get('max_iterations')) is int and flow['max_iterations'] == 1 and
            flow.get('on_fail') == 'stop' and flow.get('on_unknown') == 'review', 'Unsupported or incomplete authoring flow')
    validate_trace(loads(members[trace]))


def read(blob):
    try:
        members = _zip_members(blob)
        require('container.json' in members, 'Missing container.json')
        envelope = loads(members['container.json'])
        require(isinstance(envelope, dict) and set(envelope) == {'profile', 'kind', 'manifest', 'integrity'} and
                envelope.get('manifest') == 'manifest.json', 'Invalid envelope')
        kind = envelope.get('kind')
        require((kind == 'unit' and envelope.get('profile') == UNIT_PROFILE) or
                (kind == 'package' and envelope.get('profile') == PACKAGE_PROFILE), 'Unknown container profile')
        require(all(len(b) <= (LIMIT if kind == 'package' and p.startswith('units/') and p.endswith('.mg8')
                else MEMBER_LIMIT) for p, b in members.items()), 'Member limit')
        hashes = envelope['integrity']
        require(isinstance(hashes, dict) and set(hashes) == set(members)-{'container.json'}, 'Incomplete integrity map')
        for p, h in hashes.items():
            require(isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h) and
                    hashlib.sha256(members[p]).hexdigest() == h, 'Integrity mismatch')
        manifest = loads(members['manifest.json'])
        require(isinstance(manifest, dict), 'Invalid manifest')
        if kind == 'unit':
            _validate_unit(manifest, members)
        else:
            _validate_package(manifest, members)
        for p, b in members.items():
            if not (kind == 'package' and p.startswith('units/') and p.endswith('.mg8')):
                b.decode('utf-8')
                if p.endswith(('.json', '.gst', '.g8son', '.ork', '.qson')):
                    loads(b)
        return {'container': envelope, 'manifest': manifest, 'members': members}
    except (KeyError, TypeError, AttributeError, UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid packed resources') from exc
