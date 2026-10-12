"""Executed by the *user's* nych / mg8-engine Python interpreter, not TExporT's.

    python bridge_payload.py <request.json> <response.json>

TExporT never re-implements the encoder or the engine: every .gst, prompt and
pipeline run below comes from the installed nych and mg8-engine code. The only
thing written here that those repos don't provide yet is the initial .ork
(nych.bonit STEP 1 EMIT_INITIAL_ORK, unimplemented upstream). If nych ever ships
`nych.ork_init.emit_initial_ork`, that is used instead of the fallback below.

Standard library only at import time; nych / mg8_engine are imported lazily.
"""
import json
import re
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ORK_FORMAT_NOTE = "# FORMAT / GLYPHS_REF are proposed header fields (TExporT); not yet in ork_reference_profile v0.3"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "unit").lower()).strip("-")[:40] or "unit"


def emit_initial_ork(name, bonit, step, label, *, fmt, writer="nych_encoder", written_at=None, marker=1):
    """ORK/1.0 reference representation (mg8/spec/ork_reference_profile.md v0.3)."""
    try:  # prefer the canonical implementation once nych provides one
        from nych.ork_init import emit_initial_ork as upstream  # type: ignore
        return upstream(name, bonit, step, label, fmt=fmt, writer=writer,
                        written_at=written_at, marker=marker)
    except ImportError:
        pass
    q = lambda v: "null" if v is None else (str(v) if isinstance(v, int) else f'"{v}"')  # noqa: E731
    lines = ["ORK/1.0", f"NAME {name}", "MODE LIVE_INSTANCE", f'BONIT "{bonit}"',
             ORK_FORMAT_NOTE, f'FORMAT "{fmt}"']
    if fmt == "nych":
        lines.append('GLYPHS_REF "nych/session_invariants.py:MODALITY_OPERATORS"')
    lines += ["", "POSITION {", "  PATH [",
              f'    {{STEP {step}, LABEL "{label}", ITER null, MAX_ITERATIONS null, '
              f'EXIT_PREDICATE_REF null, ON_BOUND_EXCEEDED null}}',
              "  ]", f'  LAST_WRITER "{writer}"', f"  WRITTEN_AT {q(written_at)}", f"  MARKER {marker}", "}", ""]
    return "\n".join(lines)


def _paths(ws):
    ws = Path(ws)
    return {
        "nych_bonit": ws / "nych" / "nych.bonit",
        "mg8_bonit": ws / "mg8-engine" / "mg8.bonit",
        "tote_db": ws / "T.O.T.E-loops" / "data" / "tote_loops.sqlite3",
    }


def probe(req):
    out = {"python": sys.executable, "version": sys.version.split()[0]}
    try:
        import nych
        from nych import gst_export  # noqa: F401
        out["nych"] = {"ok": True, "path": str(Path(nych.__file__).parent),
                       "version": getattr(nych, "__version__", None)}
    except Exception as e:  # noqa: BLE001
        out["nych"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        import mg8_engine
        from mg8_engine import pipeline  # noqa: F401
        out["mg8_engine"] = {"ok": True, "path": str(Path(mg8_engine.__file__).parent)}
        try:
            from mg8_engine.llm import detect_provider, load_env
            load_env()
            out["mg8_engine"]["llm_provider"] = detect_provider()
        except Exception as e:  # noqa: BLE001
            out["mg8_engine"]["llm_provider"] = None
            out["mg8_engine"]["llm_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        out["mg8_engine"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    p = _paths(req["workspace"])
    out["files"] = {k: (str(v) if v.exists() else None) for k, v in p.items()}
    return out


def prepare(req):
    """Encode each unit with nych and write the LLM-step handoff files."""
    from nych.gst_export import build_gst
    from nych.session_invariants import SessionInvariants
    from mg8_engine.pipeline import build_congruence_prompt, build_mapping_prompt

    fmt = req["format"]                      # "nych" | "text"
    out_root = Path(req["out_dir"])
    out_root.mkdir(parents=True, exist_ok=True)
    p = _paths(req["workspace"])
    tote_db = p["tote_db"] if p["tote_db"].exists() else None
    pins_path = out_root / "session.pins.json"
    session = SessionInvariants.load(pins_path) if pins_path.exists() else SessionInvariants()
    bonit_src = p["nych_bonit"] if fmt == "nych" else p["mg8_bonit"]
    if not bonit_src.exists():
        raise FileNotFoundError(f"canonical ruleset not found: {bonit_src}")

    client = None
    if req.get("run"):
        from mg8_engine.llm import LLMClient, ensure_api_key
        if not ensure_api_key(interactive=False):
            raise RuntimeError("mg8-engine has no LLM API key configured. Run `python3 demo.py` once in "
                               "mg8-engine to set one up, then try again.")
        client = LLMClient()

    results = []
    for i, u in enumerate(req["units"], 1):
        name = f"{i:02d}-{_slug(u.get('label'))}"
        d = out_root / name
        d.mkdir(parents=True, exist_ok=True)
        gst = build_gst(u["text"], db_path=tote_db, session=session)
        if req.get("intent"):
            gst["intent"] = req["intent"]
        gst["metadata"] = {"generator": req.get("generator", "TExporT"), "format": fmt,
                           "capture": u.get("provenance", {})}
        if fmt == "text":
            gst.pop("nych_pretext", None)     # same schema; the symbolic layer only rides in NYCH format
            gst_name, step, label = "state.gst", 3, "RECEIVE_HANDOFF"
            prompt = build_congruence_prompt(gst, [])
            prompt_name = "llm_step3_congruence_prompt.txt"
        else:
            gst_name, step, label = "pretext.gst", 2, "HANDOFF"
            prompt = build_mapping_prompt(gst)
            prompt_name = "llm_step2_mapping_prompt.txt"
        (d / gst_name).write_text(json.dumps(gst, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ork_name = "nych.ork" if fmt == "nych" else "mg8.ork"
        (d / ork_name).write_text(emit_initial_ork(f"{gst['state_id']}.{name}", bonit_src.name, step, label,
                                                   fmt=fmt, written_at=_now()), encoding="utf-8")
        shutil.copyfile(bonit_src, d / bonit_src.name)
        (d / prompt_name).write_text(prompt + "\n", encoding="utf-8")
        r = {"unit": name, "dir": str(d), "state_id": gst["state_id"],
             "needs_mapping": len(gst.get("nych_pretext", {}).get("needs_mapping", [])),
             "domain": gst.get("domain"), "files": sorted(x.name for x in d.iterdir())}

        if client is not None:
            from mg8_engine.pipeline import CongruenceNotPass, PipelineError, run_pipeline
            try:
                res = run_pipeline(gst, client.mapping_llm, client.congruence_llm, d / "mg8",
                                   gate_llm=client.gate_llm, compute_tcta=(fmt == "nych"))
                for m in res["pins"]:
                    try:
                        session.pin(m["word"], m["symbol_id"])
                    except Exception:  # noqa: BLE001  (conflicting pin is reported, not forced)
                        pass
                r["run"] = {"result": "PASS", "congruence": res["congruence"].get("congruence_result"),
                            "outputs": {k: str(v) for k, v in res["paths"].items()},
                            "qson_entries": len(res["unit"].qson.entries)}
            except CongruenceNotPass as e:
                r["run"] = {"result": e.result, "reason": str(e)}
            except PipelineError as e:
                r["run"] = {"result": "REJECTED", "reason": str(e)}
            except json.JSONDecodeError as e:
                r["run"] = {"result": "REJECTED", "reason": f"LLM did not return valid JSON: {e}"}
        results.append(r)

    session.save(pins_path)
    manifest = {"generator": req.get("generator", "TExporT"), "created": _now(), "format": fmt,
                "workspace": str(req["workspace"]), "units": results,
                "next_step": ("Give each unit's prompt file to an LLM (nych.bonit STEP 2), or run with mg8-engine."
                              if fmt == "nych" else
                              "Give each unit's prompt file to an LLM (mg8.bonit STEP 3), or run with mg8-engine.")}
    (out_root / "handoff.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    if client is not None:
        manifest["tokens"] = {"in": client.total_input_tokens, "out": client.total_output_tokens,
                              "calls": client.calls}
    return manifest


def main():
    req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        res = {"ok": True, "result": {"probe": probe, "prepare": prepare}[req["cmd"]](req)}
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}
    Path(sys.argv[2]).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
