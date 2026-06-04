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
log against the lab destination.

Run `git log --oneline --grep="^feat\|^fix\|^refactor\|^test\|^docs"`
for the full implementation history.

### Environment state (as of 2026-06-16)

* **Source NetBox** (`NB_SOURCE_URL = host.docker.internal:8443`): **offline**, expected back in ~1–2 weeks (operator note 2026-06-16). The rescue loop reads from the frozen snapshot at `/workspace/snapshot-source-frozen/` until source returns.
* **Destination NetBox** (`NB_DESTINATION_URL = netbox.i.louie.se`): online; **`ENFORCE_GLOBAL_UNIQUE = False`** applied to its `configuration.py` on 2026-06-16. This flip cleared the BUG-10 cluster (14 duplicate IPs + 6 deferred `primary_ip4` patches). The flip does **not** affect IPRanges (BUG-11 still standing).
* **Container venv**: `/workspace/.devvenv/` (NOT `/workspace/.venv/`, which has Windows-mount shebangs that don't resolve in-container). Recreate with `python3 -m venv /workspace/.devvenv --clear && /workspace/.devvenv/bin/pip install -e '/workspace[dev]'` if missing.
* **Frozen snapshot vintage**: exported **before** FEAT-36-blocker shipped, so every rescue iteration needs `--allow-enum-dict-bypass` (12 files coerce 5736 rows). Tracked in BUG-09.

### Rescue iteration log

Each iteration is the audit trail for one run of the loop. Snapshot-source-frozen is the input every time.

| Iter | Phase 1 reset | Phase 2 import | Net new findings |
| :--- | :--- | :--- | :--- |
| `rescue-11` | 5047 deletes, clean | Initial baseline run; 5047 created, 104 skipped (14 IPs + 86 ipranges + 4 cables), 6 Phase-2 patches skipped | BUG-09/10/11/12/13, FEAT-50 |
| `rescue-12` | 5047 deletes, clean (FEAT-50 progress lines validated live) | Validated BUG-13 (`audit.jsonl` now carries 104 SKIPPED rows); no cross-check warning; same 104 skips | Concrete NKs for BUG-12 (4 cables on C-ESPORTS-CITY-2-SW ge-0/0/8..11) |
| `rescue-13` | 5047 deletes, clean | After destination `ENFORCE_GLOBAL_UNIQUE = False` flip: **5061 created (+14)**, 90 skipped (-14), Phase-2 **patched=130 skipped=0** (-6). 86 iprange skips persisted because the flag does not gate IPRange overlap. | BUG-14 (misleading skip text, shipped same session); BUG-10 closed |

A `rescue-14` run with no code change would reproduce rescue-13 exactly; the loop is paused at the operator boundary.

## Open

### BUG-09 — Frozen snapshot pre-dates enum-dict elimination (FEAT-36-blocker)

**Status.** DEFERRED — operator-domain, waiting on source reachability (ETA ~1–2 weeks from 2026-06-16). No code change closes this; the loop's invariant ("the frozen snapshot is read-only on disk") prevents an in-place rewrite.

**Context.** `/workspace/snapshot-source-frozen/` was exported before the import-side enum-dict elimination landed. Running `nbsnap import` without `--allow-enum-dict-bypass` aborts on the preflight; with the bypass, 12 files / 11 distinct fields coerce 5736 rows on the way in (see any of `tmp/nbsnap-rescue-{11,12,13}/import-attempt-*.log`, summary block `enum-dict bypass active: 12 files used the import-side coerce`).

Affected fields (file → field):
`dcim/cables.jsonl:status`, `dcim/device-types.jsonl:weight_unit`, `dcim/devices.jsonl:airflow`, `dcim/interfaces.jsonl:type`, `dcim/interfaces.jsonl:mode`, `dcim/locations.jsonl:status`, `dcim/racks.jsonl:status`, `dcim/sites.jsonl:status`, `extras/custom-fields.jsonl:filter_logic`, `ipam/ip-addresses.jsonl:status`, `ipam/ip-ranges.jsonl:status`.

**Why this matters.** The bypass is *forensic*, not a fix — the rescue-loop skill: "Never silence a real preflight block; the bypass is forensic, not a fix." The import coerce path recovers, but the on-disk snapshot does not round-trip cleanly (a re-export from the destination after this import produces a snapshot in the *new* shape, not matching the frozen tree). Every rescue iteration silently depends on `--allow-enum-dict-bypass`; the day someone removes the coerce, this loop breaks.

**Operator remediation path.**

1. When source comes back, refresh the frozen snapshot via the `/nbsnap-export` skill (read-only GET against source). The new tree replaces `/workspace/snapshot-source-frozen/` as one explicit operator action.
2. Run a rescue iteration **without** `--allow-enum-dict-bypass` and confirm the preflight no longer blocks. If it still blocks, the export side did not strip the enum-dict shape — file a follow-up against the export path.
3. Once step 2 passes, drop the bypass flag from the rescue-loop's documented invocation and close this ticket.

**Estimated effort.** 0 hours coding; one explicit operator gate.

### BUG-11 — 86 `ipam.iprange` rows refused as overlap

**Status.** DEFERRED — source-domain only.

**Root cause confirmed by rescue-13.** NetBox's IPRange model carries an always-on overlap check in `IPRange.clean()` for ranges in the same VRF / global table. It is **not** gated by `ENFORCE_GLOBAL_UNIQUE` (the NetBox config docs at `configuration/miscellaneous.md` are explicit that the setting covers only "prefixes and IP addresses"). Rescue-13 confirmed this empirically: with `ENFORCE_GLOBAL_UNIQUE = False` on the destination, all 86 ipranges were still refused with the same overlap text. There is no destination-side toggle that clears these.

**Per-row NKs.** Available in any rescue-13 audit row matching `category=skipped`, `child.content_type=ipam.iprange`. Sample:
```bash
jq -c 'select(.category=="skipped" and .child.content_type=="ipam.iprange") | .child.nk' \
  tmp/nbsnap-rescue-13/audit.jsonl
```
All 86 cluster in the `92.33.40.x/26` and `92.33.4x.x/26` ranges — the participant pool space.

**Why this matters.** If the destination refuses 86/101 iprange rows, every renderer that walks `ipam/ip-ranges/` for the `kea-*` roles produces empty allocations. That breaks the `kea-participant` and `kea-dist-mgmt` flows end-to-end. The 86 are very likely the intentionally-overlapping kea-participant pools (see `__reference/nb2kea/` design); removing them at source would break the renderer contract too.

**Operator remediation path** (waiting on source reachability).

1. Walk the audit list above; for each pair, decide whether the overlap is intentional (kea pool overlap pattern) or stale (data debt).
2. **If intentional**: there is no clean destination-side fix. The realistic options are (a) assign one of each overlapping pair to a non-global VRF on the source so they no longer share an address space (NetBox honors VRF isolation), or (b) accept the destination cannot mirror these ranges and document the gap in the renderer's output.
3. **If stale**: delete the overlapping row at source.
4. Re-run the rescue loop after source-side changes propagate into a fresh snapshot via `/nbsnap-export`.

**Tool-side correctness** (already shipped): BUG-13 emits per-row SKIPPED audit lines; BUG-14 corrected the misleading skip-reason text that previously claimed `ENFORCE_GLOBAL_UNIQUE` would help.

**Estimated effort.** 0 hours coding; one operator pass through 86 NKs at source.

### BUG-12 — 4 `dcim.cable` rows skipped: at least one termination did not import

**Status.** DEFERRED — operator-domain, waiting on source reachability. Per-row visibility was delivered by BUG-13; regression coverage at the upsert layer already exists in `tests/unit/test_import_skipped_incomplete.py` and `tests/unit/test_import_cable_terminations.py`. No code change closes this ticket.

**Root cause (confirmed via frozen snapshot inspection).** Four cables in `/workspace/snapshot-source-frozen/dcim/cables.jsonl` carry **empty `a_terminations`** but a valid B-termination. The pattern strongly indicates a partial cable-delete on the source: the operator removed the A-side termination from the cable but the cable row itself was never deleted. NetBox would not normally let a cable exist without both terminations; this is data debt that bypassed the model's invariants somehow (older NetBox version, direct DB edit, or migration artifact).

**Concrete NKs.** All four B-terminations land on `C-ESPORTS-CITY-2-SW`:

| Cable A side | Cable B side |
| :--- | :--- |
| (empty) | `dcim.interface` `((('c',), 'C-ESPORTS-CITY-2-SW'), 'ge-0/0/8')` |
| (empty) | `dcim.interface` `((('c',), 'C-ESPORTS-CITY-2-SW'), 'ge-0/0/9')` |
| (empty) | `dcim.interface` `((('c',), 'C-ESPORTS-CITY-2-SW'), 'ge-0/0/10')` |
| (empty) | `dcim.interface` `((('c',), 'C-ESPORTS-CITY-2-SW'), 'ge-0/0/11')` |

Confirmed in rescue-12 and rescue-13 audit.jsonl files. The four cables are **not** present on the destination (the import correctly refused them), so there is nothing to clean up on the destination side.

**Why this matters.** Four missing cables means four missing topology edges. The renderers (`netbox2cisco.py` access ⇄ dist mapping) silently ignore missing edges; cable count is a renderer-relevant signal.

**Operator remediation path** (waiting on source reachability).

1. In the source NetBox UI, navigate to **Devices → C-ESPORTS-CITY-2-SW → Interfaces** and look at `ge-0/0/8`, `ge-0/0/9`, `ge-0/0/10`, `ge-0/0/11`. Each should show a "Connected" cable with no other-end information.
2. For each, decide: delete the cable row (if the connection is genuinely gone) or restore the A-termination (if it was meant to stay).
3. Re-export the snapshot via `/nbsnap-export` after the cleanup; the next rescue iteration should land 0 cable skips.

**Estimated effort.** 0 hours coding; ~10 minutes of operator UI work once source is reachable.


## Future considerations

(none — see git history for the full implementation log)

## Completed

Per the audit on 2026-06-16, every ticket whose code has shipped
has been removed from the open backlog. Git history is the
authoritative implementation record. `git log --oneline TODO.md`
shows the audit commit and every prior body update; the matching
feat/fix/test/refactor/docs commits in `src/`, `tests/`, and
`docs/` carry the implementation detail per ticket.
