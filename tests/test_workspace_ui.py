"""Run Qt interaction in an isolated process; require an actual clean process exit."""
import os
import subprocess
import sys
from pathlib import Path


def test_offscreen_real_export_entry_organization_drop_and_package(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', HOME=str(tmp_path),
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    run = subprocess.run([sys.executable, str(Path(__file__).with_name('workspace_ui_scenario.py')), str(tmp_path)],
                         env=env, capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stdout+'\n'+run.stderr
    assert 'UI interactions complete; exiting 0' in run.stdout
