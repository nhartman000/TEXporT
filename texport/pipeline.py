"""TExporT side of the NYCH / mg8-engine integration.

Finds the user's NYCH workspace (default ~/nych-workspace, holding the nych,
mg8-engine and T.O.T.E-loops checkouts), picks the Python that has those packages
installed, and runs bridge_payload.py with it. TExporT's own Python never imports
nych or mg8_engine, so the two installs stay independent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import __version__, export

PAYLOAD = Path(__file__).with_name("bridge_payload.py")
DEFAULT_WORKSPACE = Path.home() / "nych-workspace"


class BridgeError(RuntimeError):
    pass


def workspace(cfg: dict) -> Path:
    return Path(cfg.get("nych_workspace") or DEFAULT_WORKSPACE).expanduser()


def find_python(cfg: dict) -> str:
    if cfg.get("nych_python"):
        return cfg["nych_python"]
    ws = workspace(cfg)
    for cand in (ws / "mg8-engine" / ".venv" / "bin" / "python3", ws / ".venv" / "bin" / "python3",
                 ws / "nych" / ".venv" / "bin" / "python3"):
        if cand.exists():
            return str(cand)
    return shutil.which("python3") or "python3"


def _env(cfg: dict) -> dict:
    """Also allow plain sibling checkouts that were never pip-installed (as mg8-engine's demo.py does)."""
    ws = workspace(cfg)
    extra = [p for p in (ws / "nych", ws / "mg8-engine" / "src", ws / "TCTA" / "src", ws / "TCTA") if p.exists()]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(p) for p in extra] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.pop("PYTHONHOME", None)
    return env


def call(cfg: dict, request: dict, timeout: float = 1800) -> dict:
    ws = workspace(cfg)
    request = {"workspace": str(ws), "generator": f"TExporT {__version__}", **request}
    with tempfile.TemporaryDirectory(prefix="texport-bridge-") as tmp:
        req_p, res_p = Path(tmp) / "req.json", Path(tmp) / "res.json"
        req_p.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        cwd = ws / "mg8-engine" if (ws / "mg8-engine").is_dir() else Path.home()   # .env lives here
        try:
            proc = subprocess.run([find_python(cfg), str(PAYLOAD), str(req_p), str(res_p)],
                                  cwd=cwd, env=_env(cfg), capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as e:
            raise BridgeError(f"Python for the NYCH workspace not found: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise BridgeError("The NYCH / mg8-engine step timed out.") from e
        if not res_p.exists():
            raise BridgeError((proc.stderr or proc.stdout or "bridge produced no result").strip()[-800:])
        res = json.loads(res_p.read_text(encoding="utf-8"))
    if not res.get("ok"):
        raise BridgeError(res.get("error", "unknown error"))
    return res["result"]


def probe(cfg: dict) -> dict:
    return call(cfg, {"cmd": "probe"}, timeout=120)


def units_from_sessions(sessions: list[dict], granularity: str = "marker") -> list[dict]:
    """Turn marked captures into encoder input. One unit per marker (default — the
    encoder tags each input as one utterance), or all marked text as one unit."""
    doc = export.build(sessions)
    units = []
    for r in doc["register"]:
        text = " ".join(r["text"].split())
        units.append({"label": f"M{r['n']} {r['title']}", "text": text,
                      "provenance": {"marker": f"M{r['n']}", "capture": r["title"],
                                     "lines": [r["start"] + 1, r["end"] + 1], "note": r["note"]}})
    if granularity == "combined" and units:
        return [{"label": "marked passages", "text": " ".join(u["text"] for u in units),
                 "provenance": {"markers": [u["provenance"] for u in units]}}]
    return units


def prepare(cfg: dict, sessions: list[dict], out_dir: str | Path, *, fmt: str, granularity: str,
            intent: str = "", run: bool = False) -> dict:
    units = units_from_sessions(sessions, granularity)
    if not units:
        raise BridgeError("No marked passages to encode. Mark the passages you want ingested first.")
    return call(cfg, {"cmd": "prepare", "format": fmt, "units": units, "out_dir": str(out_dir),
                      "intent": intent, "run": run})
