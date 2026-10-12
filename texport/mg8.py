"""MG8 integration: export a capture as a canonical MG8 unit and verify a
receiving model's answer against it with deterministic retention gates.

Follows the public MG8 file-family specs (github.com/nhartman000):
  .mg8 manifest (mg8_v1) · .gst state (gst_v1) · .g8son gates (g8son_v1, 1–3 gates)
  · .qson append-only trace (qson_v1) · .gitson canon bundle (gitson_v1)
  · flow.ork in the mg8-engine reference JSON profile, so mg8-engine can load the unit.

The retention gates are evaluated here with fixed, documented semantics and no
model calls: the model being checked never grades itself.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, export

PROFILE = "texport-retention-1"
GENERATOR = f"TExporT {__version__}"
CANON_REFS = [
    "https://github.com/nhartman000/mg8",
    "https://github.com/nhartman000/gst",
    "https://github.com/nhartman000/g8son",
    "https://github.com/nhartman000/qson",
    "https://github.com/nhartman000/gitson-",
    "https://github.com/nhartman000/mg8-engine",
]
GATE_IDS = ["marker_coverage", "anchor_fidelity", "no_phantom_ids"]
ID_RE = re.compile(r"\bM(\d+)\b")

PROFILE_TEXT = f"""TExporT retention profile ({PROFILE})

Purpose: check that a receiving model's response preserved the passages a person
marked in captured conversations. Evaluated deterministically by TExporT; no
model is called during evaluation.

State paths (dotted, resolved inside the unit's .gst):
  constraints.markers_required          list of marker ids, e.g. ["M1","M2"]
  internal.current.markers[*].anchors   exact strings taken from each marked passage
  external.current.response             the response text being checked

Normalization: runs of whitespace collapse to one space. Matching is case-sensitive.

Gates (file gates/retention.g8son, evaluated in order 1→3; report-only, so FAIL
routes to "continue" and every gate is always evaluated):
  1 marker_coverage  (texport.cite_all)
      PASS if every id in constraints.markers_required appears in the response as a
      whole-word token (regex \\bM<n>\\b). FAIL otherwise; the missing ids are recorded.
  2 anchor_fidelity  (texport.anchors_verbatim)
      PASS if every anchor of every marker appears verbatim in the response. FAIL
      otherwise; missing anchors are recorded per marker. INTERMEDIATE if no marker
      has any anchor (nothing deterministic to check; needs human review).
  3 no_phantom_ids   (texport.no_unknown_ids)
      PASS if every M<n> token in the response is in constraints.markers_required.
      FAIL otherwise (the response cites markers that do not exist).

Missing input: if external.current.response is empty or null, every gate is
INTERMEDIATE ("review").

Anchors are extracted when the unit is written, from each marked passage: numbers
with two or more digits or with separators (42, 3.14, 2026-10-06), dotted/pathed
identifiers (a_out.json, src/app.py), snake_case and camelCase identifiers,
ALL-CAPS terms, inline `code`, and short "quoted strings". At most 10 per marker.
"""

_ANCHOR_RE = re.compile(r"""
    `([^`\n]{1,60})`                                   # inline code
  | "([^"\n]{3,60})"                                   # short quoted string
  | (\b\d+(?:[.,:/-]\d+)+%?|\b\d{2,}%?)                # multi-digit / separated numbers
  | (\b[A-Za-z_][\w-]*(?:[./][\w-]+)+)                   # dotted or pathed identifiers
  | (\b[a-z][a-z0-9]*_[a-z0-9_]+\b)                      # snake_case
  | (\b[a-z]+[A-Z][A-Za-z0-9]+\b)                        # camelCase
  | (\b[A-Z][A-Z0-9]{1,}\b)                              # ACRONYMS
""", re.X)


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def extract_anchors(text: str, limit: int = 10) -> list[str]:
    seen, out = set(), []
    for m in _ANCHOR_RE.finditer(text or ""):
        tok = next(g for g in m.groups() if g)
        tok = _ws(tok).rstrip(".,;:")
        if not tok or ID_RE.fullmatch(tok) or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "capture").lower()).strip("-")[:40] or "capture"


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- export
def write_unit(sessions: list[dict], title: str, out_dir: str | Path, *, intent: str = "",
               export_opts: dict | None = None) -> Path:
    """Write <out_dir> as an MG8 unit directory. Returns the unit.mg8 path."""
    out = Path(out_dir)
    doc = export.build(sessions)
    unit_id = f"texport.{_slug(title)}.{uuid.uuid4().hex[:8]}"
    created = _now()

    markers = []
    for r in doc["register"]:
        span = f"line {r['start'] + 1}" if r["start"] == r["end"] else f"lines {r['start'] + 1}-{r['end'] + 1}"
        words = len(r["text"].split())
        markers.append({
            "id": f"M{r['n']}",
            "source": f"Capture {r['si'] + 1} ({r['title']}), {span}",
            "text": r["text"],
            "note": r["note"],
            "anchors": extract_anchors(r["text"]),
            "size": {"words": words, "approx_tokens": -(-len(r["text"]) // 4)},
        })
    ids = [m["id"] for m in markers]

    gst = {
        "gst_version": "1.0",
        "state_id": f"{unit_id}.state.capture",
        "internal": {"prior": {"markers": []}, "current": {"markers": markers}},
        "external": {
            "prior": {"response": None},
            "current": {
                "response": None,
                "captures": [{"title": s.get("title") or f"Capture {i + 1}", "source": s.get("source", ""),
                              "captured_at": s.get("captured_at", ""), "lines": len(s.get("lines", []))}
                             for i, s in enumerate(sessions)],
                "handoff": "handoff.md",
            },
        },
        "continuity": {"placeholder": "0,0"},
        "intent": intent.strip() or "Hand the captured conversation(s) to a receiving model without losing any marked passage.",
        "outcome_expectation": (f"The receiving model's response accounts for every marker ({ids[0]}–{ids[-1]}), "
                                "cites each by ID, and reproduces each marker's anchors verbatim.") if ids
                               else "No markers were set; nothing is required to be retained.",
        "constraints": {"profile": PROFILE, "markers_required": ids, "anchors_verbatim": True,
                        "unknown_ids_allowed": False},
        "metadata": {"generator": GENERATOR,
                     "note": "external.current.response is filled in by TExporT when a response is verified."},
    }

    outcomes = {"PASS": "continue", "FAIL": "continue", "INTERMEDIATE": "review"}
    g8son = {
        "g8son_version": "1.0",
        "file_id": f"{unit_id}.retention",
        "gates": [
            {"gate_id": "marker_coverage", "order": 1, "type": "texport.cite_all",
             "conditions": ["every id in constraints.markers_required appears in external.current.response as a whole-word token"],
             "outcomes": outcomes},
            {"gate_id": "anchor_fidelity", "order": 2, "type": "texport.anchors_verbatim",
             "conditions": ["every anchor in internal.current.markers[*].anchors appears verbatim in external.current.response"],
             "outcomes": outcomes},
            {"gate_id": "no_phantom_ids", "order": 3, "type": "texport.no_unknown_ids",
             "conditions": ["every M<n> token in external.current.response is in constraints.markers_required"],
             "outcomes": outcomes},
        ],
        "metadata": {"profile": PROFILE, "evaluator": f"{GENERATOR} (deterministic, no model calls)",
                     "semantics": "see block texport.retention-profile in canon.1.1.gitson",
                     "report_only": True},
    }

    manifest = {
        "mg8_version": "1.0",
        "unit_id": unit_id,
        "entry": "flow.ork",
        "state": ["state/capture.gst"],
        "gates": ["gates/retention.g8son"],
        "trace": "trace/execution.qson",
        "metadata": {"generator": GENERATOR, "profile": PROFILE, "title": title, "created": created,
                     "handoff": "handoff.md", "canon": "canon.1.1.gitson",
                     "description": "Captured conversation(s) with marked passages, plus retention gates "
                                    "for checking a receiving model's answer."},
    }

    gitson = {
        "gitson_version": "1.0", "bundle_id": f"{unit_id}.canon", "part": "1.1", "parts_total": 1,
        "created": created, "purpose": "canon-ingestion",
        "blocks": [{"block_id": "texport.retention-profile", "title": f"TExporT retention profile ({PROFILE})",
                    "source": GENERATOR, "content": PROFILE_TEXT}],
        "references": CANON_REFS, "demoted": [],
    }

    out.mkdir(parents=True, exist_ok=True)
    _dump(out / "unit.mg8", manifest)
    _dump(out / "flow.ork", {"ork_profile": "mg8-engine-reference-1", "flow": GATE_IDS})
    _dump(out / "state" / "capture.gst", gst)
    _dump(out / "gates" / "retention.g8son", g8son)
    _dump(out / "canon.1.1.gitson", gitson)
    (out / "trace").mkdir(exist_ok=True)
    (out / "handoff.md").write_text(export.render_markdown(sessions, title), encoding="utf-8")
    return out / "unit.mg8"


# --------------------------------------------------------------------------- verify
def _eval_gates(gst: dict, response: str) -> list[dict]:
    resp = _ws(response)
    required = gst["constraints"]["markers_required"]
    markers = gst["internal"]["current"]["markers"]
    if not resp:
        return [{"gate_id": g, "result": "INTERMEDIATE", "action": "review",
                 "reason": "no response supplied"} for g in GATE_IDS]
    cited = {f"M{n}" for n in ID_RE.findall(resp)}
    res = []

    missing = [i for i in required if i not in cited]
    res.append({"gate_id": "marker_coverage", "result": "FAIL" if missing else "PASS",
                "action": "continue", "evidence": {"missing_ids": missing, "cited_ids": sorted(cited, key=lambda x: int(x[1:]))},
                "reason": f"{len(required) - len(missing)}/{len(required)} marker ids cited"})

    per = {}
    total = found = 0
    for m in markers:
        miss = [a for a in m["anchors"] if _ws(a) not in resp]
        total += len(m["anchors"])
        found += len(m["anchors"]) - len(miss)
        if miss:
            per[m["id"]] = miss
    if total == 0:
        res.append({"gate_id": "anchor_fidelity", "result": "INTERMEDIATE", "action": "review",
                    "evidence": {"missing_anchors": {}}, "reason": "marked passages contain no checkable anchors"})
    else:
        res.append({"gate_id": "anchor_fidelity", "result": "FAIL" if per else "PASS", "action": "continue",
                    "evidence": {"missing_anchors": per}, "reason": f"{found}/{total} anchors reproduced verbatim"})

    phantom = sorted(cited - set(required), key=lambda x: int(x[1:]))
    res.append({"gate_id": "no_phantom_ids", "result": "FAIL" if phantom else "PASS", "action": "continue",
                "evidence": {"unknown_ids": phantom},
                "reason": "response cites unknown marker ids" if phantom else "no unknown marker ids"})
    return res


def _hash_event(ev: dict) -> str:
    body = {k: v for k, v in ev.items() if k != "event_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def verify(unit_path: str | Path, response: str, *, label: str = "") -> dict:
    """Evaluate the retention gates against a response. Appends nothing to existing
    files: writes a new verify-state .gst and a new .qson run under trace/.
    Returns {"results": [...], "qson": path, "state": path}."""
    unit_path = Path(unit_path)
    base = unit_path.parent
    manifest = json.loads(unit_path.read_text(encoding="utf-8"))
    if manifest.get("metadata", {}).get("profile") != PROFILE:
        raise ValueError("This MG8 unit was not written by TExporT (profile mismatch).")
    gst = json.loads((base / manifest["state"][0]).read_text(encoding="utf-8"))
    g8 = json.loads((base / manifest["gates"][0]).read_text(encoding="utf-8"))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"RUN_texport_{stamp}_{uuid.uuid4().hex[:6]}"
    sha = hashlib.sha256((response or "").encode()).hexdigest()

    # New state: prior <- the capture's external.current, current <- with the response.
    new_state_id = f"{manifest['unit_id']}.state.verify.{stamp}"
    vstate = json.loads(json.dumps(gst))
    vstate["state_id"] = new_state_id
    vstate["external"] = {"prior": gst["external"]["current"],
                          "current": {**gst["external"]["current"], "response": response,
                                      "response_sha256": sha, "label": label}}
    vstate_path = base / "state" / f"verify-{stamp}.gst"
    _dump(vstate_path, vstate)

    results = _eval_gates(vstate, response)
    events, prev = [], None

    def add(ev):
        nonlocal prev
        ev = {"trace_id": f"TRJ_{uuid.uuid4().hex}", "run_id": run_id, "sequence": len(events) + 1,
              **ev, "timestamp": _now()}
        if prev:
            ev["previous_event_hash"] = prev
        ev["event_hash"] = _hash_event(ev)
        prev = ev["event_hash"]
        events.append(ev)

    add({"event_type": "external_input", "input_state_id": gst["state_id"], "output_state_id": new_state_id,
         "actor": {"type": "human", "id": "texport-user"},
         "operation": "supply receiving-model response for retention check",
         "evidence": {"response_sha256": sha, "response_chars": len(response or ""), "label": label}})
    for r in results:
        add({"event_type": "gate_attempt", "file_id": g8["file_id"], "gate_id": r["gate_id"],
             "input_state_id": new_state_id, "result": r["result"], "action": r["action"],
             "actor": {"type": "runtime", "id": f"{GENERATOR} deterministic evaluator"},
             "reason": r["reason"], "evidence": r.get("evidence", {})})

    qson = {"qson_version": "1.0", "run_id": run_id, "events": events,
            "metadata": {"unit_id": manifest["unit_id"], "profile": PROFILE, "generator": GENERATOR,
                         "summary": {r["gate_id"]: r["result"] for r in results}}}
    qpath = base / "trace" / f"verify-{stamp}.qson"
    _dump(qpath, qson)
    return {"results": results, "qson": qpath, "state": vstate_path, "run_id": run_id}
