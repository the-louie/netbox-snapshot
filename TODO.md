# TODO

Outstanding work to deliver the NetBox portable-snapshot tool
(`nbsnap`). The phasing comes from `PLAN.md`. Every open entry is sized
for a 1, 2 hour focused work window. Each entry includes the file or
area it touches, the technical context the implementer needs, the
requirements as a concrete change list, and a testing step. Closed
items move to the Completed section at the end.

ID conventions:

* `INFRA-nn` for repo, CI, dev environment, test stack work.
* `RES-nn` for research and decision tickets that gate downstream
  implementation.
* `FEAT-nn` for feature implementation.
* `TEST-nn` for testing work that is not a side effect of a `FEAT-`.
* `DOC-nn` for documentation deliverables.
* `BUG-nn` for bug fixes.
* `REL-nn` for release and milestone gates.

Sub-tickets carry a lowercase letter suffix on the parent ID, for
example `INFRA-01a`, so a cross-reference from `PLAN.md` to the parent
concept still resolves.

Cross-references:

* `PLAN.md` for phase definitions and exit criteria.
* `docs/` for design documents.
* `docs/frictions/` for friction-area deep-dives.
* `goals.md` for scope and success criteria.

---

## Codebase status

Phases 0 through 9 are implemented and committed. The
rescue-loop skill (see `/workspace/.claude/skills/rescue-loop/SKILL.md`)
is the supported way to surface new tickets from a fresh `nbsnap import`
log against the lab destination. As of rescue iteration 11
(`tmp/nbsnap-rescue-11/`), the open backlog below carries the new
findings from that run.

Run `git log --oneline --grep="^feat\|^fix\|^refactor\|^test\|^docs"`
for the full implementation history.

## Open

### BUG-09 — Frozen snapshot pre-dates enum-dict elimination (FEAT-36-blocker)

**Status.** DEFERRED — operator-domain. Resolution depends on `NB_SOURCE_URL` becoming reachable so the frozen snapshot can be re-exported via the `/nbsnap-export` skill. No code change blocks closure; the loop's invariant ("the frozen snapshot is read-only on disk") prevents an in-place rewrite.

**Context.** `/workspace/snapshot-source-frozen/`. The pinned snapshot was exported before the import-side enum-dict elimination landed. Running `nbsnap import` against the lab destination without `--allow-enum-dict-bypass` aborts on the preflight; with the bypass, 12 files / 11 distinct fields coerce 5736 rows on the way in (see `tmp/nbsnap-rescue-11/import-attempt-2.log` summary block, `enum-dict bypass active: 12 files used the import-side coerce`).

Affected fields (file → field):
`dcim/cables.jsonl:status`, `dcim/device-types.jsonl:weight_unit`, `dcim/devices.jsonl:airflow`, `dcim/interfaces.jsonl:type`, `dcim/locations.jsonl:status`, `dcim/racks.jsonl:status`, `dcim/sites.jsonl:status`, `extras/custom-fields.jsonl:filter_logic`, `ipam/ip-addresses.jsonl:status`, `ipam/ip-ranges.jsonl:status`, plus `dcim/interfaces.jsonl:mode` (visible in audit `top offending` block).

**Why this matters.** The bypass is *forensic*, not a fix — the rescue-loop skill is explicit: "Never silence a real preflight block; the bypass is forensic, not a fix." The bypass works because the import coerce path recovers, but the on-disk snapshot will not round-trip cleanly (a re-export from the destination after this import will produce a snapshot in the *new* shape, not matching the frozen tree). Every rescue iteration is now silently dependent on `--allow-enum-dict-bypass` and `/workspace/.devvenv/bin/nbsnap` happily processing legacy enum dicts. The day someone removes the coerce, this loop breaks.

**Requirements.** This is a tracking ticket; no code change. The remediation path is:

1. When `NB_SOURCE_URL` becomes reachable again, refresh the frozen snapshot via the `/nbsnap-export` skill (read-only GET against source). The new tree replaces `/workspace/snapshot-source-frozen/` as one explicit operator action.
2. After the refresh, run a rescue iteration *without* `--allow-enum-dict-bypass` and confirm the preflight no longer blocks. If preflight still blocks, the export side did not strip the enum-dict shape — file a follow-up against the export path.
3. Leave this ticket open until step 2 passes. Do not delete the bypass flag from the rescue-loop's documented invocation until then.

**Testing.** Step 2 above is the verification. There is no unit-test surface for "the source NetBox happens to have new data shape today".

**Estimated effort.** 0 hours coding; one explicit operator gate when source returns.

### BUG-10 — 14 `ipam.ipaddress` rows refused by destination `ENFORCE_GLOBAL_UNIQUE`

**Status.** DEFERRED — operator-domain. The code-side visibility piece is delivered by BUG-13 (per-row SKIPPED audit lines now in `audit.jsonl` carry every refused NK + reason). What remains is the operator decision: either set `ENFORCE_GLOBAL_UNIQUE = False` on the destination NetBox's `configuration.py` to accept the duplicates, or de-duplicate the source data once the source is reachable. Both options are outside this codebase. The next rescue iteration's `audit.jsonl` will list the 14 refused NKs for the operator to act on.

**Context.** `src/nbsnap/import_/` upsert path for `ipam.ipaddress`. See `tmp/nbsnap-rescue-11/import-attempt-2.log` skipped block: `ipam.ipaddress: 14 (ip-address refused due to a duplicate already on the destination …)`. The source NetBox allowed duplicate IPs; the destination's `ENFORCE_GLOBAL_UNIQUE = True` refuses them. A direct consequence is six `dcim.device.primary_ip4` Phase-2 patches that skip because the target IP never landed:

```
Phase-2: target ipam.ipaddress NK=('172.16.255.5/32', 'dcim.interface', ((('d',), 'D-MIRAGE-PALACE-SW'), 'lo0.0')) still missing, skipping dcim.device.primary_ip4
…and 5 more (172.16.255.4/32, .6/32, .7/32, .8/32, .9/32, all on the d-region access switches' lo0.0)
```

These are all `/32` loopbacks on `lo0.0` for d-region access switches, which strongly suggests the destination already has the same loopback addresses from a previous partial import that wasn't fully wiped, OR the source genuinely has duplicate global IPs across two devices (legitimate in some lab/management contexts).

**Why this matters.** Without these primary_ip4 patches landing, the six d-region switches end up with no `primary_ip4` set on the destination, which breaks the renderer contract (`netbox2kea.py` reads `device.primary_ip4`).

**Requirements.**

1. Pull the audit log for the 14 refused IPs by re-running `nbsnap import` with `--audit-out` and a fresh wipe, then jsonl-grep for `ipam.ipaddress` SKIPPED entries — *blocked by BUG-13 below; today the audit log does not carry SKIPPED rows*. Workaround for this ticket: parse them out of the textual `import-attempt-2.log` summary plus the six explicit Phase-2 skip lines.
2. For each refused IP, decide: (a) is this a genuine source-side duplicate the operator wants? — if yes, the only path is to set `ENFORCE_GLOBAL_UNIQUE = False` on the destination via `configuration.py` (operator-domain change, not a code change here) and re-run; (b) if not, the source row is stale and should be removed at the source (when reachable) — until then, document the loss in the rescue-iteration README.
3. Update the import side so the SKIPPED summary line includes the NK of every refused row (today the summary only carries the count and the reason). Append to `src/nbsnap/summary.py`.

**Testing.**

- Extend `tests/unit/test_import_skip_enforcement.py` (create if absent) with a stub HTTP client that returns `HTTP 400 {"address": ["Duplicate IP address found in global table: …"]}` for an `ipam.ipaddress` POST. Assert (i) the row counts as SKIPPED, (ii) the per-row NK appears in the SKIPPED summary line, (iii) downstream Phase-2 patches that depend on the row skip cleanly without raising.
- Run `pytest tests/unit/ --ignore=tests/unit/test_pack.py --ignore=tests/unit/test_cli.py --ignore=tests/unit/test_reset_cli_skeleton.py -q`.

**Estimated effort.** 1–2 hours for the summary change + test. The operator decision in step 2 is out-of-band.

### BUG-11 — 86 `ipam.iprange` rows refused as overlap by destination `ENFORCE_GLOBAL_UNIQUE`

**Status.** DEFERRED — operator-domain. Same shape as BUG-10. BUG-13 delivers the per-row visibility in `audit.jsonl`; the remaining decision (relax `ENFORCE_GLOBAL_UNIQUE` on the destination vs. remove overlapping rows at the source) is outside this codebase.

**Context.** `src/nbsnap/import_/` upsert path for `ipam.iprange`. See `tmp/nbsnap-rescue-11/import-attempt-2.log`: `ipam.iprange: 86 (iprange refused due to overlap with an existing range. The source NetBox allowed this overlap; the destination's ENFORCE_GLOBAL_UNIQUE policy refuses it.)`. Same root cause as BUG-10 (destination policy mismatch), but the count is much higher (86 vs 14), so the source almost certainly has *intentional* overlapping ranges (kea-participant pools that overlap kea-dist-mgmt ranges by design — that's how the renderers in `__reference/nb2kea/` build pool allocations).

**Why this matters.** If the destination refuses 86/101 iprange rows, every renderer that walks `ipam/ip-ranges/` for the `kea-*` roles will produce empty allocations. That breaks the `kea-participant` and `kea-dist-mgmt` flows end-to-end on the destination.

**Requirements.**

1. Confirm the source intent by walking `__reference/nb2kea/reference_documentation/architecture_notes/` for any note on intentional overlap. (`07-naming-and-netbox-mapping.md` is the most likely.)
2. If overlap is intentional (expected): the destination NetBox `configuration.py` needs `ENFORCE_GLOBAL_UNIQUE = False` and a re-run. This is an operator-domain configuration change; surface to the user via the rescue-iteration README rather than silently working around it.
3. The same summary improvement as BUG-10 applies — per-row NK in the SKIPPED summary line is what makes this debuggable next iteration.

**Testing.** Same shape as BUG-10: stub HTTP returns `HTTP 400 {"start_address|end_address": ["… overlaps with existing range …"]}` and the test asserts the SKIPPED bucket carries the NK in its summary.

**Estimated effort.** 1 hour for the summary side (shared with BUG-10). Operator-side config change is out-of-band.

### BUG-12 — 4 `dcim.cable` rows skipped: at least one termination did not import

**Status.** DEFERRED — operator-domain. The per-row visibility piece is delivered by BUG-13 (`audit.jsonl` now lists every skipped cable's NK). The existing regression coverage (`tests/unit/test_import_skipped_incomplete.py`, `tests/unit/test_import_cable_terminations.py`) already pins the skip behaviour at the upsert layer, so there is no remaining code change. What remains is operator-side: walk the four NKs in `audit.jsonl`, check whether each cable's missing termination is genuinely absent from `dcim/interfaces.jsonl` (stale source row → remove at source) or whether the import refused the interface (file a fresh ticket against the import path with the specific NK). This is one-off triage, not a recurring bug.

**Context.** `src/nbsnap/import_/` upsert path for `dcim.cable`. See `tmp/nbsnap-rescue-11/import-attempt-2.log`: `dcim.cable: 4 (cable body has no resolvable terminations on at least one side, skipping; the source row's interface endpoints did not import successfully)`. The cable section ran *after* `dcim.interface` (which completed cleanly — 3582 interfaces, 0 failed in the per-section line). So the four "unresolved terminations" cables either (a) reference interfaces that legitimately don't exist on the source — a snapshot data integrity issue, or (b) reference interfaces whose parent device was skipped further upstream — cascade from BUG-10's missing devices.

The six d-region switches with skipped `primary_ip4` (BUG-10) are still imported as devices, so their interfaces should exist. That makes (a) more likely: stale cable rows in the source pointing at interfaces that were deleted.

**Why this matters.** Four missing cables means four missing topology edges, which the renderers (`netbox2cisco.py` access ⇄ dist mapping) silently ignore. Cable count is a renderer-relevant signal.

**Requirements.**

1. Identify the four skipped cables: re-run the import once more, pipe the `dcim.cable …skipping` log line through a grep that extracts each cable's NK, write the NKs into a comment on this ticket. *Blocked by BUG-13's per-row visibility today; workaround is increasing log verbosity at the cable upsert site (`src/nbsnap/import_/upsert_runner.py` or wherever the cable phase emits the skip).*
2. For each NK, inspect `dcim/cables.jsonl` in the frozen snapshot for the row, then resolve both termination NKs against `dcim/interfaces.jsonl`. If either side is missing from the snapshot, the source row is stale → document and skip. If both sides are present but the destination didn't accept the interface, that's an upstream import-side bug → file a follow-up.

**Testing.**

- Add a unit test in `tests/unit/test_import_cable_unresolved.py`: stub interface upserts so only one side of a two-sided cable lands, assert the cable row counts as SKIPPED with `cable body has no resolvable terminations on at least one side` in the reason, *and* per-row NK in the SKIPPED summary (same shape as BUG-10/BUG-11).
- Full unit suite.

**Estimated effort.** 1–2 hours.


## Future considerations

(none — see git history for the full implementation log)

## Completed

Per the audit on 2026-06-16, every ticket whose code has shipped
has been removed from the open backlog. Git history is the
authoritative implementation record. `git log --oneline TODO.md`
shows the audit commit and every prior body update; the matching
feat/fix/test/refactor/docs commits in `src/`, `tests/`, and
`docs/` carry the implementation detail per ticket.
