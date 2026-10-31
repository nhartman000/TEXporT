"""Regression coverage for unchanged legacy exporter and NYCH input seam; no model calls."""
import json
from texport import export, mg8, pipeline
from test_icm import SESSIONS


def test_original_directory_retention_export_and_evaluator(tmp_path):
    path = mg8.write_unit(SESSIONS, 'Legacy preserved', tmp_path/'legacy')
    assert path.read_bytes().startswith(b'{')  # Still original JSON manifest, NOT packed ZIP.
    manifest = json.loads(path.read_text())
    assert manifest['metadata']['profile'] == 'texport-retention-1'
    assert (path.parent/'handoff.md').read_text() == export.render_markdown(SESSIONS, 'Legacy preserved')
    results = mg8.verify(path, 'M1 Keep EXACT 42; M2 Check budget')['results']
    assert [r['result'] for r in results] == ['PASS', 'PASS', 'PASS']
    assert mg8.verify(path, '')['results'][0]['result'] == 'INTERMEDIATE'


def test_original_nych_marker_selection_is_not_replaced_by_icm_state():
    units = pipeline.units_from_sessions(SESSIONS)
    assert [u['provenance']['marker'] for u in units] == ['M1', 'M2']
    assert units[0]['text'] == 'Keep EXACT 42'
    assert units[0]['provenance']['note'] == 'precise'
    assert 'Full surrounding context' not in units[0]['text']
    combined = pipeline.units_from_sessions(SESSIONS, 'combined')
    assert len(combined) == 1 and combined[0]['text'] == 'Keep EXACT 42 Check budget'
