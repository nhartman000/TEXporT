import io
import zipfile
from texport import icm


def test_generated_workspace_has_scoped_icm_routing_contracts():
    sessions = [{'id': 'scope-test', 'title': 'Source', 'source': 'offline fixture',
                 'lines': [{'text': 'Retain the approved limit.'}],
                 'markers': [{'start': 0, 'end': 0, 'note': 'Review requirement'}]}]
    plan = icm.direct_plan(sessions, 'Scope test')
    for gated in [False, True]:
        plan['groups'][0]['conditions'] = ([{'text': 'Retain the approved limit.', 'marker_ids': ['M1']}] if gated else [])
        with zipfile.ZipFile(io.BytesIO(icm.workspace(sessions, plan, approved=True))) as z:
            for path in ['CONTEXT.md', 'sources/CONTEXT.md', 'themes/uncategorized/CONTEXT.md']:
                body = z.read(path).decode()
                for heading in ['## Inputs', '## Process', '## Outputs', '## Check']:
                    assert heading in body, (path, heading)
            routes = z.read('AGENTS.md').decode()
            assert 'themes/uncategorized/CONTEXT.md' in routes
            assert 'sources/CONTEXT.md' in routes
            assert '[M1.txt](M1.txt)' in z.read('themes/uncategorized/CONTEXT.md').decode()
            if gated:
                assert 'unit.mg8' in z.read('themes/uncategorized/CONTEXT.md').decode()
