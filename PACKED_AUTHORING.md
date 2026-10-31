# ICM workspace and packed authoring API

Open **Export… → Export ICM / AI organization / packed MG8…**. This is separate
from the existing Markdown/PDF, directory retention-unit/Verify and canonical NYCH
bridge. Their code paths and encoder are unchanged. The new authoring path does
not call a model, discover credentials, execute gates, or run an agent.

## Organization and review

1. Select captures in the visible Export list. ICM uses that explicit list order.
2. Use direct organization (all markers start in `uncategorized`), editing the JSON
   to define themes/actions. `g8son:` notes are visible review hints, **not gates**.
3. Alternatively **Copy AI organization prompt**, paste into an existing chat AI,
   then paste its JSON response into the proposal editor. This is a two-step
   manual, offline-to-the-app workflow, not a built-in provider integration.
4. Preview/validate, review source membership and conditions, check approval, then
   save a **new** workspace ZIP. Any edit invalidates preview and approval.

Only the following JSON structure is accepted:

```json
{
  "icm_version": "1.0",
  "title": "Budget review",
  "gestalt": null,
  "variables": {"budget": null},
  "groups": [{
    "name": "budget",
    "label": "Budget review",
    "marker_ids": ["M1"],
    "conditions": [{"text": "Confirm the stated budget", "marker_ids": ["M1"]}]
  }]
}
```

Every known marker must belong to **exactly one** group; unknown IDs, dropped
markers, duplicate membership, extra proposal fields, arbitrary paths/code fields
and conditions citing outside their group are rejected. Conditions are plain
human-review requirements, never executable expressions. Captured instructions
and imported text are untrusted data. Approval authorizes **authoring only**.
Names use 1–64 ASCII alphanumeric/underscore/hyphen characters, unique ignoring
case. Limits: 32 groups, 1024 markers, 96 conditions/group, 128 scalar variables,
1 MiB proposal; labels/title/condition strings at most 4096 characters. Unknown
values stay `null`; no guessed state is inserted. No model results are fabricated.

Workspace ZIP contents: README.md, AGENTS.md, CONTEXT.md, organization.json,
sources/compiled-markers.txt (existing full-context renderer with global M IDs),
sources/snapshot.json (original sessions/provenance), state/global.gst, and named
theme folders containing labelled verbatim marker `.txt` files. Themes with
approved conditions also contain GST/G8SON/ORK/QSON authoring companions and a
complete independent `unit.mg8`. More than three gates split into additional
G8SON files; themes without conditions remain text-only. GST retains the full
source snapshot, notes, capture IDs/timestamps and global gestalt/context/variables.

## Packed MG8 / MG8PK

These files are ZIP **STORE**, not renamed JSON manifests. Each includes
`container.json` declaring `mg8-packed/1.0` or `mg8pk-packed/1.0`,
`manifest.json` and SHA-256 integrity for **every other member**. Packed units
contain complete referenced GST, G8SON, ORK and QSON resources. They are not
accepted by the old directory-unit retention evaluator simply by changing a suffix.

Generated unit ORK profile: `texport-packed-authoring/1.0`, all gate IDs in
explicit order, `max_iterations: 1`, `on_fail: stop`, `on_unknown: review`.
The initial QSON event is an actual **review/assembly** event with human authoring
actor, distinct run/trace IDs and sequence 1 — no invented PASS, gate attempt or
state commit. A saved container is a snapshot, not a trusted append-only database.
Gate import preserves existing event identities and appends an authoring review.

In **Edit packed MG8**, load a unit, drop one or several external `.g8son` files
(or use Add files), review the member list and explicit appended gate order,
approve and save a **new** `.mg8`. Original unit and gate files are not modified.
Exact imported gate bytes and file/gate IDs are retained. Collisions fail closed.
Unsupported ORK grammar is rejected, never silently translated.

In **Ordered MG8PK**, add complete packed units in the selected order, use Up/Down
or Remove, preview the chain, approve and save new `.mg8pk`. Units retain their
bytes and must have unique unit IDs. The `mg8pk-sequential/1.0` system.ork flow
matches manifest order and max_steps equals the bounded unit count. Counts 4, 6,
8 and 12 are exercised. Package-within-package is rejected.

**Both new ORK profiles are data-only authoring topologies.** No existing runtime
integration, evaluation, state handoff, branching, improved reasoning, or future
runtime compatibility is promised. Nothing is automatically executed.

### Security profile

16 MiB archive AND total member bytes; 128 members; 1 MiB unit/resource members;
package `units/*.mg8` members may be 16 MiB subject to the outer total limit;
1–32 package units. ZIP64, comments/trailing data, split volumes, data descriptors,
encryption, compression, extensions, symlink/special entries, duplicates and
case-insensitive path collisions are rejected. Names use portable ASCII
`[A-Za-z0-9_-][A-Za-z0-9_./-]*`, with no empty/dot/traversal segments.
Local and central identity, method, size and CRC must agree; CRC and SHA-256 are
verified before authoring. Strict UTF-8 JSON rejects duplicate keys, NaN and
infinite/overflowing numbers. Resource references must resolve internally and
have the expected suffix, unique gate/file identities, bounded valid schemas and
supported authoring flow. No extraction, eval, dynamic imports or network requests
occur. Hashes establish byte integrity, not origin/authenticity/authorization.
The UI saves via an atomic exclusive link, refusing overwrite (including symlinks).

### Shared GST and audit boundary (app / extension)

The interoperable GST profile remains the full state shape below, not a compact
`context`/`gestalt` replacement. `gst_version` is `1.0`; `state_id` identifies this
state snapshot (not a run, file or gate). Both `internal` and `external` contain
explicit `prior` and `current` snapshots. `continuity` is an object retaining
continuity information; the author's `{"placeholder":"0,0"}` is unresolved data,
not evidence of runtime continuity. `intent` and `outcome_expectation` are nonblank
strings; `constraints` is an object defining authoring limits, not permissions.

The app puts source-linked markers and `global: {gestalt, context, variables}` in
`internal.current`, and original `sessions` in `external.current`. Prior snapshots
are initially `{}`; missing gestalt/variables remain `null`. Preserve these full
resources byte-for-byte during gate import. No state commit is implied.

Shared packed bounds: resource paths at most **240 ASCII characters**; unit,
package, GST state, G8SON file/gate and QSON run/trace identities at most **200
characters**, nonblank. Blank string gate conditions are rejected. Unit ORK has
exactly `ork_profile`, `flow`, `max_iterations`, `on_fail`, `on_unknown`; package
ORK replaces `max_iterations` with `max_steps`. Extra branches/parallel directives
are rejected before import. An approved unit flow may be any complete permutation
of its unique gate IDs; import appends after that reviewed order, never sorts it
back to manifest/file enumeration.

QSON events bind to the enclosing `run_id`, use unique `trace_id`s and contiguous
integer sequences `1..n` (booleans are not sequences). Canonical optional fields
retain their schema types, including nullable action/operation/output state,
unique references and object metadata; arbitrary event/actor extension fields
remain allowed by the canonical schema. An optional timestamp must be an actual
RFC3339 date-time with `Z` or a numeric timezone, not a naive date-time. The writer
emits current UTC timestamps; no execution evidence is invented.

Workspace ZIP writers also enforce 16 MiB total member bytes **and physical ZIP
length including headers**, 128 entries, and 1 MiB per loose resource (complete
`.mg8` members may be up to 16 MiB within the outer limit). A valid proposal can
still exceed output bounds, in which case export fails rather than dropping data.

## Importable interop API (standard library, Python >=3.10)

From repository root, `from texport import packed, icm`:

- `packed.dump(value) -> bytes`: UTF-8 finite JSON serialization.
- `packed.loads(bytes_or_str) -> value`: strict UTF-8/duplicate-key/finite JSON parser.
- `packed.validate_gates(bytes) -> dict`: validate one bounded canonical G8SON file.
- `packed.create_unit(unit_id, gst_dict, gate_files, metadata=None) -> bytes`:
  `gate_files` is an insertion-ordered dict `{ "gates/name.g8son": exact_bytes }`.
  Generates complete unit resources, explicit flow and real assembly review event.
- `packed.read(bytes) -> {container, manifest, members}`: validates unit/package and
  nested complete units; `members` maps exact archive paths to exact bytes.
  Raises `ValueError` on invalid input. It never extracts or executes anything.
- `packed.add_gates(unit_bytes, files) -> NEW bytes`: `files` is insertion-ordered
  `{ "name.g8son": exact_bytes }`; basename only. Appends flow in that order,
  preserves originals and existing events, adds one review event. Caller must
  preview/approve before publication. No filesystem writes.
- `packed.create_package(package_id, units, metadata=None) -> bytes`:
  insertion-ordered `{ "units/name.mg8": complete_unit_bytes }`; explicit order,
  unique unit identities and bounded count. No runtime execution.
- `icm.direct_plan(sessions, title) -> dict`,
  `icm.proposal_prompt(sessions, title) -> str`,
  `icm.validate_plan(sessions, dict_or_json_bytes_or_str) -> cloned dict`,
  `icm.workspace(sessions, plan, approved=True) -> ZIP bytes`.
- For native publishing only: `texport.workspace_dialog.save_new(path, bytes)`
  atomically publishes without overwrite; requires Qt module availability.

Example read:

```python
from pathlib import Path
from texport import packed
u = packed.read(Path("examples/packed/example.mg8").read_bytes())
print(u["manifest"]["unit_id"], list(u["members"]))
p = packed.read(Path("examples/packed/four.mg8pk").read_bytes())
assert len(p["manifest"]["units"]) == 4
```

Generated fixtures under `examples/packed/` are explicit illustrative authoring
inputs, **not AI output or execution evidence**. `example.mg8` is also the exact
first nested unit of `four.mg8pk`. `workspace.zip`, sessions.json and proposal.json
show source-preserving organization. The extra gate is a byte-preservation fixture.

## Reproduce tests

Use a dedicated environment, install pytest and PySide6, then from repo root:

```sh
QT_QPA_PLATFORM=offscreen python -m pytest tests -q
python tests/test_autoscroll.py
```

Existing auto-scroll script has module-level assertions (baseline pytest collected
no named tests; exit 5 after those assertions). The full new pytest suite still
imports/exercises them and the script is also run directly. UI tests use a separate
isolated HOME/process and require exit 0, not just a printed success. Only native
file pickers/fixture session discovery are substituted; actual Qt clicks, real
ZIPs, proposal validation, gate drop events and package reorder/save are exercised.
No live screen capture, OCR service, provider request or NYCH runtime is tested.
RED/GREEN transcripts are under `evidence/`; `full-tests.log` is the final suite.
