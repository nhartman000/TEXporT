from pathlib import Path
import pytest
from texport import packed

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('field', ['unit', 'state', 'file', 'gate', 'run', 'trace', 'package', 'actor',
                                    'event_file', 'event_gate', 'input_state', 'output_state'])
@pytest.mark.parametrize('control', ['\x00', '\x1f', '\x7f'])
def test_container_identifiers_reject_control_characters(field, control):
    original = (ROOT/'examples/packed/example.mg8').read_bytes()
    source = packed.read(original)
    members = {k:v for k,v in source['members'].items() if k != 'container.json'}
    manifest = source['manifest']
    bad = 'bad'+control+'id'
    if field == 'package':
        package = packed.read((ROOT/'examples/packed/four.mg8pk').read_bytes())
        members = {k:v for k,v in package['members'].items() if k != 'container.json'}
        package['manifest']['package_id'] = bad
        members['manifest.json'] = packed.dump(package['manifest'])
    elif field == 'unit':
        manifest['unit_id'] = bad; members['manifest.json'] = packed.dump(manifest)
    elif field == 'state':
        p = manifest['state'][0]; o = packed.loads(members[p]);o['state_id'] = bad;members[p] = packed.dump(o)
    elif field in ['file','gate']:
        p = manifest['gates'][0]; o = packed.loads(members[p])
        if field == 'file':o['file_id'] = bad
        else:
            old=o['gates'][0]['gate_id'];o['gates'][0]['gate_id']=bad
            flow=packed.loads(members[manifest['entry']]);flow['flow']=[bad if g==old else g for g in flow['flow']]
            members[manifest['entry']]=packed.dump(flow)
        members[p]=packed.dump(o)
    else:
        p=manifest['trace'];o=packed.loads(members[p]);ev=o['events'][0]
        if field=='run':
            o['run_id']=bad
            for event in o['events']:event['run_id']=bad
        elif field=='trace':ev['trace_id']=bad
        elif field=='actor':ev['actor']['id']=bad
        else:ev[{'event_file':'file_id','event_gate':'gate_id','input_state':'input_state_id','output_state':'output_state_id'}[field]]=bad
        members[p]=packed.dump(o)
    blob=packed.archive(members,'package' if field=='package' else 'unit')
    with pytest.raises(ValueError):packed.read(blob)


def test_actor_identifier_bound_matches_other_identifiers():
    u=packed.read((ROOT/'examples/packed/example.mg8').read_bytes())
    members={k:v for k,v in u['members'].items() if k!='container.json'}
    p=u['manifest']['trace'];q=packed.loads(members[p]);q['events'][0]['actor']['id']='a'*201
    members[p]=packed.dump(q)
    with pytest.raises(ValueError):packed.read(packed.archive(members))
