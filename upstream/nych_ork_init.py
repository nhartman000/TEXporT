"""
ork_init.py -- nych.bonit STEP 1 EMIT_INITIAL_ORK (proposed for the nych repo).

Drop this file into nych/ as nych/ork_init.py. TExporT already calls
`nych.ork_init.emit_initial_ork` when it exists and otherwise uses an identical
built-in copy, so adding it here makes nych the single owner of the initial .ork
(nych.bonit AUTHORITY: nych_encoder ROLE ... ork_init).

Output follows mg8/spec/ork_reference_profile.md v0.3 (ORK/1.0). FORMAT and
GLYPHS_REF are proposed header fields, not yet in the profile -- approve them
in the spec (or delete these two lines) before treating this as canon.
"""

ORK_FORMAT_NOTE = "# FORMAT / GLYPHS_REF are proposed header fields (TExporT); not yet in ork_reference_profile v0.3"


def emit_initial_ork(name, bonit, step, label, *, fmt, writer="nych_encoder", written_at=None, marker=1):
    """ORK/1.0 reference representation (mg8/spec/ork_reference_profile.md v0.3)."""
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
