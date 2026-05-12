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
* `BUG-nn` for bug fixes (none open yet, reserved).
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

## Open, Phase 0, Foundation

### [INFRA-01a] Create pyproject.toml with project metadata

**Context:** `PLAN.md` Phase 0 names Python 3.11+ as the runtime floor.
We need a build manifest at the repo root before any code lands so
later phases can `pip install -e .` cleanly.

**Requirements:**

- Create `/workspace/pyproject.toml`.
- `[project]` table with `name = "nbsnap"`, `version = "0.0.1"`,
  `description`, `requires-python = ">=3.11"`,
  `license = "BSD-3-Clause"`.
- `[project.authors]` and `[project.maintainers]` both set to
  `[{name = "Louie", email = "louie@louie.se"}]`.
- `[project.scripts]`: `nbsnap = "nbsnap.cli:main"`.
- `[project.entry-points."nbsnap.plugin"]`: empty block with an
  explanatory comment, reserved for `FEAT-31`.
- `[build-system]`: `requires = ["hatchling"]`,
  `build-backend = "hatchling.build"`.
- Add a comment at the top naming the reasons for the 3.11 floor
  (`tomllib`, PEP 695 type aliases, exception groups).
- Do not add any third-party runtime deps yet, those land per
  `RES-01`.
- Create `/workspace/LICENSE` at repo root with the SPDX
  BSD-3-Clause text, copyright line
  `Copyright (c) 2026 Louie <louie@louie.se>`.

**Testing:** install `hatchling` in a venv, run
`python -m build --sdist --wheel`, confirm a wheel and an sdist land
under `dist/`. Then run `pip install --dry-run -e .` and confirm the
`nbsnap` script entry-point is reported.

**Estimated Effort:** 1-2h

### [INFRA-01b] Add ruff and mypy configuration to pyproject.toml

**Context:** lint and type-check posture must be settled before
`INFRA-04a` can wire the CI lint job. Style baseline picks 100-column
lines and Python 3.11 target.

**Requirements:**

- `[tool.ruff]`: `line-length = 100`, `target-version = "py311"`.
- `[tool.ruff.lint]`: `select = ["E", "F", "W", "I", "B", "UP", "ARG",
  "SIM"]`.
- `[tool.ruff.format]`: defaults are fine, set `quote-style = "double"`.
- `[tool.mypy]`: `strict = true`, `python_version = "3.11"`,
  `warn_unused_ignores = true`, `files = ["src/nbsnap"]`.
- `[[tool.mypy.overrides]]` block relaxing strictness on `tests.*`
  (allow missing return types).
- Add `dev` optional dependency group with `ruff`, `mypy`, `pytest`,
  `pytest-cov`, `pre-commit`.

**Testing:** drop a one-line module under `src/nbsnap/_smoke.py` with a
deliberate unused import, run `ruff check src/`, confirm the unused
import is flagged. Drop a function with no type annotations into the
same file, run `mypy src/`, confirm the missing return type is flagged.
Delete the smoke file before commit.

**Estimated Effort:** 1-2h

### [INFRA-01c] Create README.md with project overview and links

**Context:** without a README, contributors land on `CLAUDE.md` which
is agent-facing. The README is the human entry-point.

**Requirements:**

- Create `/workspace/README.md`.
- First content after the H1 title is the production-read-only
  banner (the same blockquote that opens `CLAUDE.md`). Operators
  reading only the README must see the constraint before any
  workflow guidance. Link back to `CLAUDE.md` for the canonical
  source so the two cannot drift undetected.
- One paragraph project description, snapshot tool, API only.
- Section "Status", flagging that the project is in design phase,
  pointer to `PLAN.md` Phase 0 for current work.
- Section "Documentation", linking `PLAN.md`, `goals.md`,
  `docs/INDEX.md`, `docs/frictions/00-overview.md`.
- Section "Quick start" with the env var convention from `CLAUDE.md`
  (the four `NB_SOURCE_*` / `NB_DESTINATION_*` vars), and the
  intended `nbsnap export` / `nbsnap import` invocation shapes.
- No duplication of `CLAUDE.md` beyond the banner, link to it for
  the agent context.

**Testing:** open the README in a Markdown renderer (`glow`,
`mdcat`, or GitHub preview), click every link, confirm each target
file exists in this repo. Confirm the env var names match
`.claude/settings.json` and `CLAUDE.md` exactly (grep for
`NB_SOURCE_URL`).

**Estimated Effort:** 1-2h

### [INFRA-02a] Create empty src/nbsnap/ package tree

**Context:** `PLAN.md` Phase 0 layout. Empty importable packages
unblock every later `FEAT-` ticket since they can drop their module
into the right sub-package.

**Requirements:**

- `src/nbsnap/__init__.py` with `__version__ = "0.0.1"` and a one-line
  module docstring.
- Create `src/nbsnap/{http,schema,graph,natkey,export,import_,verify,
  plugins}/__init__.py` with a one-line module docstring each.
- `import_` carries the trailing underscore because `import` is a
  reserved word.
- `src/nbsnap/py.typed` empty marker file (PEP 561) so type info
  ships with the package.
- `[tool.hatch.build.targets.wheel]` set to `packages = ["src/nbsnap"]`.

**Testing:** run `pip install -e .` then
`python -c "from nbsnap import http, schema, graph, natkey, export,
import_, verify, plugins; print(nbsnap.__version__)"` and confirm
`0.0.1` prints with no `ImportError`.

**Estimated Effort:** 1-2h

### [INFRA-02b] Wire argparse CLI skeleton with stub sub-commands

**Context:** `PLAN.md` Phase 0 exit criterion names `nbsnap --help`
running against an empty repo. Sub-command stubs let later phases land
features one by one without breaking the CLI surface.

**Requirements:**

- `src/nbsnap/cli.py` with `main(argv: list[str] | None = None) -> int`.
- Top-level `argparse.ArgumentParser` with `--version`,
  `--verbose / -v`, `--quiet / -q`.
- Sub-parsers for `export`, `import`, `plan`, `diff`, `verify`,
  `verify-natkeys`, `pack`, `unpack`. `pack` and `unpack` route to
  the real implementations in `FEAT-34` / `FEAT-35` once those
  land.
- Each sub-command handler is a stub function that prints a
  "not implemented yet, tracked in `<ticket-id>`" message to stderr
  and returns exit code 2. The stubs name the implementing ticket
  (`FEAT-17a` for export, `FEAT-25a` for import, `FEAT-07a` for
  plan, `FEAT-26b` for diff, `FEAT-27b` for verify, `FEAT-10b` for
  verify-natkeys, `FEAT-34` for pack, `FEAT-35` for unpack).
- `--version` reads `nbsnap.__version__`.
- `if __name__ == "__main__": sys.exit(main())` block.

**Testing:** install in editable mode, run `nbsnap --help`, confirm
all eight sub-commands appear. Run `nbsnap --version`, confirm
`0.0.1`. Run `nbsnap export`, confirm exit code 2 and the stub
message names a ticket id.

**Estimated Effort:** 1-2h

### [INFRA-02c] Implement .env auto-loader in config.py

**Context:** the `nb2kea` reference uses `_load_dotenv_if_present` to
let operators stash credentials in `/workspace/.env`. We replicate the
pattern so `nbsnap` finds the four-variable env without a CLI flag.

**Requirements:**

- `src/nbsnap/config.py` with `load_dotenv(start: Path | None = None)
  -> Path | None`.
- Walk up from `start` (default `Path.cwd()`) looking for `.env`.
- Parse `KEY=VALUE` lines, skip blanks and lines starting with `#`.
- No quoting or variable substitution, the format matches `nb2kea`.
- Only set `os.environ[k] = v` if `k not in os.environ`, so an
  explicit shell export wins.
- Return the loaded path or `None`.
- Call `load_dotenv()` from `cli.main` before any other logic.

**Testing:** unit test in `tests/unit/test_config.py` covering three
cases. Case 1, `.env` in a temp dir with `FOO=bar`, chdir into it,
loader runs, assert `os.environ["FOO"] == "bar"`. Case 2, pre-set
`FOO=baz` in env, run loader against the same `.env`, assert `FOO`
stays `baz`. Case 3, `.env` in parent dir, cwd in a subdir, confirm
loader walks up and finds it.

**Estimated Effort:** 1-2h

### [INFRA-03a] Vendor docker-compose for the source NetBox test instance

**Context:** `PLAN.md` Phase 0 names the two-instance test stack. We
start with source. Port 8080 picks a value clear of the operator's
live `host.docker.internal:8443` so a dev cannot run the test seeder
against production by accident.

**Requirements:**

- Create `tests/fixtures/source/docker-compose.yml` copied from
  `netbox-community/netbox-docker`, pinned to the highest 4.6.x tag
  available at implementation time. The implementer confirms the
  tag's NetBox version matches the production source's
  `GET /api/status/` response before pinning, then records the exact
  tag in a top-level `NETBOX_DOCKER_TAG` Make variable and notes the
  choice in `tests/fixtures/README.md` with the date and source
  NetBox version. Future bumps go through a PR that updates
  `NETBOX_DOCKER_TAG` and re-runs the integration suite.
- Bind NetBox HTTP on host port 8080.
- Volume names suffixed `-source` so volumes do not collide with the
  destination compose.
- `tests/fixtures/source/env/netbox.env` with admin user
  `admin@example.invalid`, fixed admin token `0123456789abcdef0123456789abcdef01234567`,
  fixed `SECRET_KEY` (not production-grade, fine for tests).
- `tests/fixtures/README.md` with a callout: "test stack on 8080 and
  8081, do not confuse with the operator's production NetBox on
  `host.docker.internal:8443`".

**Testing:** run `docker compose -f tests/fixtures/source/docker-compose.yml
up -d`, poll `http://localhost:8080/api/status/` with the fixed admin
token, confirm a 200 with a JSON body inside 90 seconds. Run
`docker compose ... down -v`, confirm no `nbsnap-source-*` volume
remains via `docker volume ls`.

**Estimated Effort:** 1-2h

### [INFRA-03b] Vendor docker-compose for the destination NetBox test instance

**Context:** mirror of `INFRA-03a` with a port and volume namespace
shift so the two stacks run side by side.

**Requirements:**

- `tests/fixtures/dest/docker-compose.yml` copied from `source/`.
- Host port 8081.
- Volume name suffix `-dest`.
- `tests/fixtures/dest/env/netbox.env` with a different admin token
  and a different `SECRET_KEY`.
- Update `tests/fixtures/README.md` with the dest endpoint and a note
  that the two NetBoxes must have different `SECRET_KEY` values so
  the password-hash portability friction (`docs/frictions/07`) can be
  exercised in tests.

**Testing:** with the source stack already up, bring the dest stack
up. Confirm `curl -H 'Authorization: Token <dest-token>'
http://localhost:8081/api/status/` returns 200. Confirm the two
NetBoxes report different installation UUIDs in the status payload.
Tear both down, confirm no volume leak.

**Estimated Effort:** 1-2h

### [INFRA-03c] Add Makefile targets for stack lifecycle

**Context:** developers need a one-command spinup, CI calls the same
targets. Put the orchestration in a Makefile rather than a bash
script so target names stay discoverable.

**Requirements:**

- `Makefile` at repo root with phony targets `stack-up`, `stack-down`,
  `stack-wait`, `stack-seed`, `stack-status`.
- `stack-up` brings both compose stacks up with `--project-name
  nbsnap-source` and `nbsnap-dest` so concurrent dev runs do not
  collide.
- `stack-wait` polls both `/api/status/` endpoints with a 90-second
  cap, exits non-zero on timeout.
- `stack-down` tears both down with `-v`.
- `stack-seed` invokes `python tests/fixtures/seed.py` (the next
  ticket).
- `stack-status` runs `docker compose ps` for both stacks.
- Use `docker compose` (v2), not the legacy `docker-compose` script.

**Testing:** run `make stack-up stack-wait`, confirm both NetBoxes
reachable in under 90 seconds. Run `make stack-up` a second time,
confirm idempotent behaviour (no errors). Run `make stack-down`,
confirm cleanup. Run `make stack-status` after teardown, confirm it
reports no containers.

**Estimated Effort:** 1-2h

### [INFRA-03d] Write Python seeder for tests/fixtures/seed/*.json

**Context:** integration tests need a known starting state. The
seeder is shared across the source and destination stacks so a single
fixture set drives the round-trip tests.

**Requirements:**

- `tests/fixtures/seed.py` accepts `--url`, `--token`, `--dir`
  arguments.
- Reads every `*.json` file from `--dir` in lexical order.
- Each file is a list of `{"endpoint": "<api-path>", "payload":
  {...}}` objects.
- POST each payload, on HTTP 400 with a "unique constraint" message
  treat as a soft warning and continue (so the seeder is idempotent).
- Ship one seed file `tests/fixtures/seed/00-roles.json` defining one
  `dcim.devicerole` with slug `access_switch`, so the seeder has
  something to do.
- `make stack-seed` calls this script against both stacks with their
  matching tokens.

**Testing:** seed against a fresh source stack, run
`curl http://localhost:8080/api/dcim/device-roles/?slug=access_switch`,
confirm exactly one match. Re-run the seeder, confirm no errors and
still one match (idempotency).

**Estimated Effort:** 1-2h

### [INFRA-03e] Sites and Locations seed fixture

**Context:** the test stack needs at least one Site and one Location
before any Device fixture can land. Splits out from the original
`INFRA-03d` placeholder per the Q6 burndown decision.

**Requirements:**

- Create `tests/fixtures/seed/01-sites.json` with one Site `hall-d`
  (`name = "Hall D"`).
- Create `tests/fixtures/seed/02-locations.json` with one Location
  `the-forge` (`name = "The Forge"`, `site = "hall-d"`).
- Both files follow the seeder format established in `INFRA-03d`
  (`[{"endpoint": "...", "payload": {...}}]`).
- Document the dependency on `INFRA-03d` running first (the seeder
  applies files in lexical order, the numbering enforces order).

**Testing:** run `make stack-seed`, confirm
`GET /api/dcim/sites/?slug=hall-d` and
`GET /api/dcim/locations/?slug=the-forge` each return one match on
both stacks. Re-run, confirm idempotent.

**Estimated Effort:** 1-2h

### [INFRA-03f] Devices and Interfaces seed fixture

**Context:** Device fixtures need a Manufacturer and DeviceType
chain. Splits out from `INFRA-03d` per Q6.

**Requirements:**

- Create `tests/fixtures/seed/03-manufacturers.json` with
  Manufacturer `cisco`.
- Create `tests/fixtures/seed/04-device-types.json` with DeviceType
  `cisco/ws-c2950t-24`.
- Create `tests/fixtures/seed/05-devices.json` with two Devices
  `d39a` and `d39b` (role `access_switch`, site `hall-d`, location
  `the-forge`, device_type `cisco/ws-c2950t-24`).
- Create `tests/fixtures/seed/06-interfaces.json` with one
  management Interface per Device (`Vlan600`, type
  `virtual`).
- Depends on `INFRA-03d` (role) and `INFRA-03e` (site, location)
  having run first.

**Testing:** run `make stack-seed`, confirm
`GET /api/dcim/devices/?name=d39a` returns one match with the right
site, location, and device_type. Confirm
`GET /api/dcim/interfaces/?device=d39a` returns one Vlan600
interface. Re-run, confirm idempotent.

**Estimated Effort:** 1-2h

### [INFRA-03g1] VLAN, Prefix, IPRange, IPAddress seed fixtures

**Context:** the IPAM half of the addressing seed. Lands the four
fixture files that introduce the management subnet and per-device
addresses. The patch step that wires `primary_ip4` onto the
Device fixtures lives in `INFRA-03g2`.

**Requirements:**

- Create `tests/fixtures/seed/07-vlans.json` with one VLAN
  `vlan-600` (`vid = 600`, `name = "MGMT"`).
- Create `tests/fixtures/seed/08-prefixes.json` with one Prefix
  `172.16.1.0/24` (vlan `vlan-600`).
- Create `tests/fixtures/seed/09-ip-ranges.json` with one IP Range
  inside the Prefix (start `172.16.1.100`, end `172.16.1.254`).
- Create `tests/fixtures/seed/10-ip-addresses.json` with two
  IPAddresses (`172.16.1.10/24`, `172.16.1.11/24`), each assigned to
  the matching Device's Vlan600 Interface from `INFRA-03f`. Use
  the seeder's interface-lookup-by-`(device.name, name)` helper.
- Update `tests/fixtures/README.md` so the dependency on
  `INFRA-03f` (interfaces exist) is documented.

**Testing:** run `make stack-seed` after `INFRA-03f` has run,
confirm `GET /api/ipam/vlans/?vid=600` returns one match,
`GET /api/ipam/prefixes/?prefix=172.16.1.0/24` returns one,
`GET /api/ipam/ip-addresses/?address=172.16.1.10/24` returns one
match assigned to `d39a`'s Vlan600 interface. Re-run, confirm
idempotent.

**Estimated Effort:** 1-2h

### [INFRA-03g2] primary_ip4 patch step on Device fixtures

**Context:** Devices need their `primary_ip4` field populated
**after** the IPAddresses from `INFRA-03g1` exist. The seeder
handles this with a dedicated patch step that runs at the end of
lexical order so the IPAddress ids are already known.

**Requirements:**

- Create `tests/fixtures/seed/12-device-primary-ips.json` with two
  patch entries, each of the form
  `{"endpoint": "dcim/devices/<device.name>/",
  "method": "PATCH", "payload": {"primary_ip4":
  {"_resolve": ["ipam.ipaddress", "172.16.1.10/24"]}}}`.
- Extend the seeder script from `INFRA-03d` to interpret
  `{"_resolve": [content_type, lookup_value]}` placeholders by
  looking up the destination id and substituting before the PATCH
  fires. The substitution layer is shared by `INFRA-03h` cabling
  fixtures (which also need the resolver).
- The patch step must be idempotent, re-running the seeder against
  a stack where `primary_ip4` already matches is a no-op (compare
  current value before issuing PATCH).

**Testing:** run `make stack-seed` end-to-end, confirm
`GET /api/dcim/devices/?name=d39a` returns
`primary_ip4.address == "172.16.1.10/24"`. Re-run the seeder,
inspect the audit log (the seeder prints a per-row outcome),
confirm the PATCH step reports `NOOP` on the second run.

**Estimated Effort:** 1-2h

### [INFRA-03h] Cabling seed fixture

**Context:** renderer-parity tests need a Cable between the two
access switches to exercise the polymorphic cable termination
path. Splits out from `INFRA-03d` per Q6.

**Requirements:**

- Create `tests/fixtures/seed/11-cables.json` with one Cable
  connecting `d39a:Gi0/2` to `d39b:Gi0/2` (or to a synthetic dist
  switch port if the fixture later grows a dist).
- Each termination encoded as
  `{"object_type": "dcim.interface", "object_id": <id>}`. Since the
  seeder cannot know ids upfront, the seeder resolves the ids by
  looking up the interfaces by `(device.name, name)` before
  POSTing the cable.
- Depends on `INFRA-03f` (interfaces exist).

**Testing:** run `make stack-seed`, confirm
`GET /api/dcim/cables/` returns one cable with both terminations
resolving to the seeded interfaces. Re-run, confirm idempotent.

**Estimated Effort:** 1-2h

### [INFRA-04a] GitHub Actions workflow, lint job

**Context:** `PLAN.md` Phase 0 CI baseline. Lint is the cheapest job
and gates the rest.

**Requirements:**

- `.github/workflows/ci.yml` with `on: [pull_request, push]`,
  `push.branches: [main]`.
- Job `lint` on `ubuntu-latest`, Python 3.11.
- Steps: checkout, `setup-python` with pip cache keyed on
  `pyproject.toml`, `pip install -e ".[dev]"`.
- Run `ruff check .`, `ruff format --check .`, `mypy src/` as
  separate steps so per-step failures are visible in the CI UI.
- Concurrency group cancels in-flight runs on a new push to the same
  PR.

**Testing:** create a branch with a deliberate ruff issue (unused
import), push, observe lint job failing on the ruff step. Push the
fix, confirm green. Add a deliberately untyped function in `src/`,
push, confirm mypy step fails.

**Estimated Effort:** 1-2h

### [INFRA-04b] GitHub Actions workflow, unit job with Python matrix

**Context:** unit tests must pass on 3.11 and 3.12 to protect against
language drift between the two supported floors.

**Requirements:**

- Add `unit` job to `.github/workflows/ci.yml`.
- Matrix: `python-version: ["3.11", "3.12"]`, `fail-fast: false`.
- Steps: checkout, `setup-python` with cache, install editable.
- Run `pytest tests/unit -q --strict-markers --strict-config`.
- Upload `pytest` JUnit XML as an artefact.

**Testing:** add `tests/unit/test_smoke.py` with one passing test
asserting `nbsnap.__version__ == "0.0.1"`. Push, confirm matrix runs
both Python versions and both pass. Add a deliberately failing test,
confirm both matrix entries fail.

**Estimated Effort:** 1-2h

### [INFRA-04c] GitHub Actions workflow, integration job with docker compose

**Context:** integration tests need the two-instance stack. Skipped on
draft PRs to save CI minutes (the stack is heavy).

**Requirements:**

- Add `integration` job to `.github/workflows/ci.yml`.
- Trigger: pull_request types `[opened, synchronize, ready_for_review]`,
  with `if: github.event.pull_request.draft == false`.
- Steps: checkout, `setup-python`, set up buildx with GHA cache
  scope `gha-netbox`, install editable, `make stack-up stack-wait
  stack-seed`.
- Run `pytest tests/integration -q --strict-markers`.
- `if: always()` step running `make stack-down` so failed runs do not
  leak containers.
- Job timeout 30 minutes.

**Testing:** add `tests/integration/test_smoke.py` that hits
`GET /api/status/` on both stacks with `requests` or stdlib, asserts
200 on both. Push branch with PR ready-for-review, confirm the
integration job runs and passes. Mark PR as draft, confirm the job is
skipped on the next push.

**Estimated Effort:** 1-2h

### [INFRA-04d] Pre-commit config matching the lint job

**Context:** developers should not round-trip through CI to find
formatter issues that pre-commit can catch locally.

**Requirements:**

- `.pre-commit-config.yaml` at repo root.
- Hooks: `ruff` (with `--fix`), `ruff-format`, `trailing-whitespace`,
  `end-of-file-fixer`, `check-yaml`, `check-toml`,
  `check-merge-conflict`.
- Mypy stays out of pre-commit, it is too slow for an interactive
  commit, CI catches it.
- Add a note in `README.md` for the install (`pip install pre-commit;
  pre-commit install`).

**Testing:** install pre-commit hooks. Add a trailing whitespace in a
markdown file and try to commit, confirm the hook fixes and re-stages.
Run `pre-commit run --all-files` on a clean tree and confirm zero
issues.

**Estimated Effort:** 1-2h

### [RES-01] Decide HTTP client library

**Context:** `FEAT-01a` to `FEAT-01f` all depend on this choice.
Candidates are `httpx` (HTTP/2, async-capable, clean retry hooks),
`requests` (ubiquitous, no HTTP/2), stdlib `urllib.request` (zero
dep, verbose). The `nb2kea` route used `curl` via subprocess, which
we are stepping away from.

**Requirements:**

- Author `docs/implementation/01-http-client.md`.
- Compare the three options on: timeout precision, retry/backoff
  hooks, HTTP/2 support, dependency weight, type stubs, async path,
  TLS-verify-toggle ergonomics.
- Land a single decision with a "what would force a flip" line.
- Add the chosen library to `pyproject.toml` `[project]
  dependencies`, not the dev group (it is a runtime dep).
- Link the decision doc from `PLAN.md` Phase 1 scope and
  `docs/frictions/10-api-scaling-and-rate-limits.md`.

**Testing:** self-review confirms the doc names every trade-off and
the chosen library has a concrete rejection criterion. One teammate
sign-off in the PR thread. Spot check: run `pip install ".[*]"` from
a clean venv and confirm the chosen library imports.

**Estimated Effort:** 1-2h

### [RES-02] Decide sync vs async runtime model

**Context:** `docs/05-export-import-workflow.md` defaults to
single-worker. Async pays nothing under single-worker but unblocks
later parallel read passes. `httpx` carries both modes behind the
same API, so the door stays open either way.

**Requirements:**

- Author `docs/implementation/02-runtime.md`.
- Recommend sync v1 with a short note on the async migration path.
- Capture the cost of async at this stage (debugging complexity,
  context vars, exception group propagation).
- Cross-link from `PLAN.md` Phase 1, Phase 8.

**Testing:** self-review confirms the doc lists the future-async
trigger condition (a measurement, not a hunch). Confirm `httpx` API
sketches in the doc work in both sync and async by adapting a 20-line
prototype.

**Estimated Effort:** 1-2h

### [RES-03] Decide snapshot file format on disk

**Context:** `docs/04-snapshot-format.md` picked JSONL per object
type. Storage form (raw vs gzip vs tarball) and pack/unpack ergonomics
are still open.

**Requirements:**

- Author `docs/implementation/03-snapshot-storage.md`.
- Recommend raw JSONL as the default for `git diff` friendliness.
- Ship `nbsnap pack` / `nbsnap unpack` sub-commands (already stubbed
  in `INFRA-02b`) for the tarball form, document the format.
- Document the SHA over the canonicalised contents for integrity
  checks on the packed form.
- Define a clear naming for packed snapshots (`<name>.nbsnap.tar.zst`).

**Testing:** self-review confirms the recommendation covers the
diff-experience, the share-as-one-artefact, and the cold-storage
cases. Confirm `zstd` is available in the chosen runtime image
(present in the CI runner base).

**Estimated Effort:** 1-2h

---

## Open, Phase 1, Schema discovery

### [FEAT-01a] HTTP client class skeleton with auth precedence

**Context:** `src/nbsnap/http/client.py`. Mirrors `nb2kea`
`NetboxClient` but uses the library from `RES-01`. The auth
precedence rule is `CLAUDE.md` "Environment & endpoints".

**Requirements:**

- `class NetboxHTTP` with `__init__(self, base_url, token, *,
  timeout=30, verify_tls=True, page_size=500, max_retries=3,
  backoff=(0.5, 1.5, 3.0), allow_writes=True)`.
- **Source read-only guard rail, layer 1 (per Q8 burndown).** When
  `base_url` matches `NB_SOURCE_URL` on a host-and-port substring
  basis, the constructor forces `allow_writes=False` regardless of
  the kwarg. The kwarg cannot override the source-URL match. Log a
  one-line INFO event on construction stating "read-only client
  bound to source NetBox".
- Class method `from_env(role: Literal["source", "destination"], *,
  url=None, token=None, **overrides) -> NetboxHTTP` resolving in the
  order: explicit kwarg, then role-specific env var, then legacy
  `NB_URL`/`NB_TOKEN`. When `role == "source"`, pass
  `allow_writes=False` explicitly so the intent is visible in code.
- Import-time call to `config.load_dotenv()`.
- Method stubs: `get_one`, `get_all`, `post`, `patch` raising
  `NotImplementedError` (transport lands in `FEAT-01b`).
- Repr that masks the token (`token=***<last-4>`) and includes
  `allow_writes` so debugging surfaces the posture.

**Testing:** unit test in `tests/unit/test_http_client_auth.py`.
Five cases. Case 1, only `NB_SOURCE_TOKEN` set, `from_env("source")`
picks it. Case 2, both `NB_SOURCE_TOKEN` and `NB_TOKEN` set,
source wins. Case 3, explicit `token="kw"` overrides everything.
Case 4, `from_env("source")` returns a client with
`allow_writes=False` regardless of the kwarg. Case 5, a manual
`NetboxHTTP(base_url=NB_SOURCE_URL, token=..., allow_writes=True)`
still produces a read-only client (constructor override). Confirm
`repr()` does not contain the full token and does contain the
`allow_writes` flag.

**Estimated Effort:** 1-2h

### [FEAT-01b] GET/POST/PATCH transport with JSON handling

**Context:** continues `FEAT-01a`. Lands the actual library calls and
status-code routing without retry logic yet (retry lands in `FEAT-01d`).

**Requirements:**

- Implement `_request(method, path, *, json=None) -> dict | None`.
- **Source read-only guard rail, layer 2 (per Q8 burndown).** Before
  any socket activity, if `self.allow_writes is False` and `method`
  is not `"GET"` or `"HEAD"`, raise `SourceWriteForbidden` with a
  message naming the URL and the verb. This is the second layer
  over the constructor refusal from `FEAT-01a`, defence in depth.
- Compose absolute URL from `base_url` and the relative `path`.
- Set `Authorization: Token <token>` header (NetBox convention).
- Set `Accept: application/json`, `Content-Type: application/json` on
  POST/PATCH.
- Raise `HTTPError` on >=400, with the response body included in the
  exception message.
- On 204 No Content return `None`, on 2xx with body parse JSON.
- `get_one(path)`, `post(path, body)`, `patch(path, body)` are
  one-liner wrappers.
- `get_all(path)` is still a stub raising `NotImplementedError`,
  pagination is `FEAT-01c`.

**Testing:** unit test in `tests/unit/test_http_client_transport.py`
using `respx` (httpx) or `responses` (requests) to mock. Cover: GET
200 returns parsed JSON, POST 201 returns parsed JSON, PATCH 204
returns None, GET 400 raises with body in the message. Confirm the
Authorization header is set on every request. Add one source-side
test, instantiate with the source URL, attempt POST, confirm
`SourceWriteForbidden` is raised before any HTTP mock fires.

**Estimated Effort:** 1-2h

### [FEAT-01c] Pagination iterator following the next link

**Context:** NetBox paginates list endpoints. `docs/frictions/10` M2
forbids `?limit=0`, we follow `next` instead. Default page size 500
from `RES-01` discussion.

**Requirements:**

- Implement `get_all(self, path) -> Iterator[dict]`.
- Append `?limit=<page_size>` (or merge with existing query string).
- Yield rows from `results`, follow `next` until absent.
- Track the running total against the first response's `count`
  field, log a warning if they disagree at the end.
- Method `get_all_with_progress(path) -> Iterator[tuple[int, int,
  dict]]` returning `(index, total, row)` so callers can show
  progress without a second pass.

**Testing:** unit test in `tests/unit/test_http_client_pagination.py`
mocking three pages of 2 rows each with `next` linkage. Confirm the
iterator yields 6 rows in order. Confirm a `count` mismatch (server
claims 7, returns 6) emits a warning.

**Estimated Effort:** 1-2h

### [FEAT-01d] Retry envelope honouring Retry-After and backoff

**Context:** `docs/frictions/10` M4 and M5. Retry on curl-equivalent
exits, HTTP 429, HTTP 5xx, no-status. Do not retry on 4xx other than
429. Cap at 3 retries.

**Requirements:**

- Wrap `_request` in a `_request_with_retries`.
- Retry decision rule mirrors `nb2kea` `_is_retryable`.
- On HTTP 429 with `Retry-After`, parse the value. Try integer
  seconds first. On parse failure, fall back to HTTP-date format
  via `email.utils.parsedate_to_datetime` (stdlib, no extra dep).
  Compute the wait as `target - now` clamped to a non-negative
  number. Both formats per Q9 burndown.
- On 5xx and connection errors, sleep per backoff schedule
  `(0.5, 1.5, 3.0)`, reusing the last element if the schedule is
  shorter than `max_retries`.
- Emit a warning per retry attempt with the URL, status, and the
  upcoming wait.
- Replace `_request` callers with the wrapped variant.

**Testing:** unit test in `tests/unit/test_http_client_retry.py`.
Case 1, server returns 503 twice then 200, confirm two retries then
success. Case 2, server returns 429 with `Retry-After: 1`, confirm
the wait happens (mock `time.sleep` to assert called with 1.0).
Case 3, server returns 429 with
`Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`, mock the current time
to one hour earlier, confirm the wait is approximately 3600
seconds. Case 4, server returns 400, confirm no retry (one call).
Case 5, server returns 503 five times, confirm exactly 3 retries
then a raised error.

**Estimated Effort:** 1-2h

### [FEAT-01e] TLS verification toggle

**Context:** `CLAUDE.md` "Environment & endpoints", the local source
`host.docker.internal:8443` uses a self-signed cert, the public
destination keeps verify on.

**Requirements:**

- Plumb `verify_tls` through to the library's client (`httpx.Client(verify=...)`
  or equivalent).
- When `verify_tls=False`, suppress the urllib3 InsecureRequestWarning
  once at import time.
- CLI flag `--no-verify-tls` already in `FEAT-17`, `FEAT-25` plumbs
  the value through.
- Log a one-line warning at INFO level on construction when
  `verify_tls=False` so operators see they are running unverified.

**Testing:** unit test in `tests/unit/test_http_client_tls.py`. Mock
the client constructor, instantiate `NetboxHTTP` with `verify_tls=False`,
assert the underlying client was created with `verify=False`. Repeat
with `verify_tls=True`, assert `verify=True`. Confirm the warning
fires only when `verify_tls=False`.

**Estimated Effort:** 1-2h

### [FEAT-01f] Per-page auto-shrink on consecutive timeouts

**Context:** `docs/frictions/10` M1. A 500-page that times out should
back down to 200, then 50, before the call as a whole fails.

**Requirements:**

- Track the last successful page size on the instance.
- On a curl-equivalent timeout for a paginated GET, halve the page
  size (`500 -> 200 -> 50`) and retry.
- Once a shrunk page succeeds, cache the new size for the rest of
  this run.
- Floor of 25 (below that the timeout is not a page-size problem).
- Counts as a retry against `max_retries`.

**Testing:** unit test in `tests/unit/test_http_client_pagination_shrink.py`.
Mock a server that times out on `limit=500` and `limit=200` but
returns success on `limit=50`. Confirm `get_all` issues three calls
in order with shrinking limits, then completes. Assert the cached
size on the instance is now 50.

**Estimated Effort:** 1-2h

### [FEAT-01g1] SourceWriteForbidden exception and is_source_url helper

**Context:** the two-layer guard rail from `FEAT-01a` and
`FEAT-01b` consults a shared exception and a shared URL-match
helper. This ticket lands those primitives so the other layers can
import them without circular references.

**Requirements:**

- Create `src/nbsnap/http/guard.py`.
- `class SourceWriteForbidden(Exception)` whose `__str__` names
  the URL, the verb, and a one-line pointer back to the
  `CLAUDE.md` production-read-only banner.
- Helper `is_source_url(base_url: str, source_url: str | None =
  None) -> bool`. Reads `os.environ.get("NB_SOURCE_URL")` when
  `source_url` is `None`. Compares the host-and-port portion via
  `urllib.parse.urlsplit`, substring match on the host:port form
  so trailing slashes and `/api` suffixes do not bypass.
- Module-level constant `READ_ONLY_VERBS = frozenset({"GET",
  "HEAD", "OPTIONS"})` for the second layer to consult.

**Testing:** unit test in `tests/unit/test_guard_helper.py`
covering, `is_source_url` matches
`https://host.docker.internal:8443/` against
`NB_SOURCE_URL=https://host.docker.internal:8443`. Matches
`https://host.docker.internal:8443/api/dcim/devices/`. Does not
match `https://other-host:8443/`. Returns `False` when
`NB_SOURCE_URL` is unset and `source_url` arg is `None`.
`SourceWriteForbidden("POST", "https://h/x")` formats a message
that contains both the verb and the URL.

**Estimated Effort:** 1-2h

### [FEAT-01g2] Wire the guard rail attribute and method into NetboxHTTP

**Context:** the constructor refusal and the request envelope
refusal from `FEAT-01a` / `FEAT-01b` both consult a cached
`_is_source` attribute. This ticket adds the attribute and the
public diagnostic method so the layers compute it once.

**Requirements:**

- Extend `src/nbsnap/http/client.py` `NetboxHTTP.__init__` to
  compute `self._is_source = is_source_url(base_url)` once after
  `base_url` is stored.
- When `self._is_source is True`, force `self._allow_writes =
  False` regardless of the kwarg, per `FEAT-01a` layer 1 behaviour
  (refresh the wiring to consult `self._is_source`).
- Public method `is_source(self) -> bool` returns
  `self._is_source`.
- Update `_request` from `FEAT-01b` to raise
  `SourceWriteForbidden(method, self._base_url)` when
  `self._is_source and method not in READ_ONLY_VERBS`. The check
  fires before any HTTP work.
- Update `__repr__` to surface `self._is_source` alongside the
  existing `allow_writes` field, so debugging surfaces the posture.

**Testing:** unit test in
`tests/unit/test_http_client_is_source.py`. Instantiate
`NetboxHTTP(base_url="https://host.docker.internal:8443/api/", ...)`,
confirm `.is_source() is True`, confirm `repr()` contains
`is_source=True`. Instantiate against the destination URL, confirm
`.is_source() is False`. Test that the kwarg `allow_writes=True`
does not override the source detection.

**Estimated Effort:** 1-2h

### [FEAT-01g3] Source-readonly end-to-end integration test with socket mocking

**Context:** the guard rail must refuse non-GET requests against
the source URL **before** any network activity. An end-to-end
integration test using a `socket.socket` monkeypatch proves the
refusal fires early.

**Requirements:**

- Create `tests/integration/test_source_readonly_e2e.py`.
- Use `pytest.fixture` to monkeypatch `socket.socket.__init__` with
  a counter, every constructor invocation increments the counter.
- Build a real `NetboxHTTP` against `NB_SOURCE_URL` with
  `verify_tls=False`. Reset the counter to zero.
- Attempt `client.post("dcim/sites/", body={"name": "test"})`.
- Assert that `SourceWriteForbidden` is raised.
- Assert that the socket constructor counter stayed at zero, no
  socket was opened.
- Repeat for `patch`, `put`, `delete`.
- Add one positive control, `client.get_one("status/")` against the
  source URL, assert the call completes, socket counter is
  positive.

**Testing:** the integration test above is the testing step. Run
`pytest tests/integration/test_source_readonly_e2e.py -q` against
the source stack started by `make stack-up`. Confirm green.

**Estimated Effort:** 1-2h

### [FEAT-02a] Fetch OpenAPI schema and cache to disk

**Context:** `docs/03-dependency-graph.md` and
`docs/04-snapshot-format.md` both consume the live OpenAPI schema. A
single GET, body up to several MB.

**Requirements:**

- `src/nbsnap/schema/openapi.py` with `class OpenAPI` wrapping a
  parsed dict.
- Class method `OpenAPI.fetch(http) -> OpenAPI`, single
  `http.get_one("schema/?format=json")` call.
- Class method `OpenAPI.load(path) -> OpenAPI`, reads from disk.
- Method `dump(path)`, writes canonicalised JSON with `sort_keys=True,
  indent=None`.
- Method `hash() -> str`, returns sha256 hex of the canonicalised
  bytes.
- Module-level `SCHEMA_PATH = "schema/openapi.json"` constant for
  snapshot layout consumers.

**Testing:** unit test in `tests/unit/test_openapi_io.py`. Round-trip
a small fixture schema through `dump` and `load`, assert equality.
Confirm `hash()` is stable across two `dump`/`load` cycles. Integration
test in `tests/integration/test_openapi_fetch.py` against the source
stack: fetch, dump, reload, confirm the hashes match.

**Estimated Effort:** 1-2h

### [FEAT-02b] iter_endpoints traversal helper

**Context:** `FEAT-05` and `FEAT-11` both walk every paths entry. We
land the helper once.

**Requirements:**

- Method `iter_endpoints() -> Iterator[Endpoint]`.
- `Endpoint` dataclass: `path: str`, `methods: dict[str,
  Operation]`, `content_type: str | None` (derived per Q10).
- Skip non-API paths (anything outside `/api/`).
- For each `path` group its operations by HTTP method.
- Resolve `$ref` entries lazily, helper `_resolve_ref(ref) -> dict`.
- **Content type derivation (per Q10 burndown).** Two-layer:
  1. URL convention, `/api/<app>/<plural-model>/ ->
     <app>.<singular-model>`. Hyphen-aware singularisation rule,
     `device-roles -> devicerole`, `ip-addresses -> ipaddress`.
     Generic plural strip (`s`, `es`, `ies -> y`).
  2. Curated exceptions table at module top, currently covering
     `dcim/device-roles/ -> dcim.devicerole`,
     `dcim/device-types/ -> dcim.devicetype`,
     `dcim/virtual-chassis/ -> dcim.virtualchassis`,
     `dcim/front-ports/`, `dcim/rear-ports/`,
     `dcim/console-ports/`, `dcim/console-server-ports/`,
     `ipam/ip-addresses/ -> ipam.ipaddress`,
     `ipam/ip-ranges/ -> ipam.iprange`,
     `ipam/asn-ranges/ -> ipam.asnrange`,
     `ipam/route-targets/ -> ipam.routetarget`,
     `ipam/fhrp-groups/ -> ipam.fhrpgroup`,
     `extras/custom-fields/`,
     `extras/custom-field-choice-sets/`.
     Table entries take precedence over the convention.

**Testing:** unit test in `tests/unit/test_openapi_iter_endpoints.py`
with a hand-crafted minimal OpenAPI doc covering one list endpoint,
one detail endpoint, and one `$ref` use. Confirm `iter_endpoints()`
yields both, with operations bucketed correctly, and the `$ref`
resolves to the referenced schema.

**Estimated Effort:** 1-2h

### [FEAT-02c] field_spec parser for FK metadata

**Context:** the cycle detector (`FEAT-06`) and the export FK
rewriter (`FEAT-12`) need per-field shape: nullable, required, FK
target, m2m, write-allowed.

**Requirements:**

- Method `field_spec(content_type: str, field: str) -> FieldSpec`.
- `FieldSpec` dataclass: `nullable: bool`, `required: bool`,
  `fk_target: str | None` (content type, or `None` for non-FK),
  `is_m2m: bool`, `write_allowed: bool`.
- Determine `write_allowed` per Q12, see `FEAT-02d`.
- **Determine `fk_target` (per Q11 burndown).** Three-layer
  detection:
  1. Direct `$ref`. If the field's schema is a `$ref` pointing at
     a model schema, resolve to `app.model` via the same name
     pattern used in layer 2.
  2. NetBox schema name pattern. NetBox emits inline schemas
     named `BriefX`, `NestedX`, `XBrief` for FK targets. Strip the
     prefix or suffix, look up the resulting model name in a
     `{ModelName: content_type}` reverse index built once at
     module load from `iter_endpoints` results.
  3. Hand-curated exception table for fields that defy both
     signals. Initially empty, grows with NetBox releases that
     break the pattern.
- Helper `_reverse_index()` builds the `{ModelName: content_type}`
  map by walking endpoints.
- Method `m2m_fields(content_type) -> list[str]` and
  `fk_fields(content_type) -> list[str]` as convenience wrappers.

**Testing:** unit test in `tests/unit/test_openapi_field_spec.py`
with a fixture schema for `dcim.device`. Confirm `field_spec("dcim.device",
"primary_ip4")` returns `nullable=True`, `required=False`,
`fk_target="ipam.ipaddress"`. Confirm `field_spec("dcim.device",
"id")` returns `write_allowed=False` (server-set). Confirm
`field_spec("dcim.device", "tags")` has `is_m2m=True`.

**Estimated Effort:** 1-2h

### [FEAT-02d] write_allowlist computation

**Context:** `docs/02-data-model-scope.md` "Field-level allowlist
policy". Every per-type export needs the set of fields the
destination's PATCH body accepts.

**Requirements:**

- Method `write_allowlist(content_type) -> frozenset[str]`.
- Cache the result per content_type on the instance, since callers
  invoke it once per record.
- **Compute as the union of POST request-body fields and PATCH
  request-body fields per Q12 burndown.** Some fields are only
  accepted on POST (create-time-only fields), some only on PATCH
  (update-only fields). Union lets the snapshot carry any field
  either verb accepts. The importer's POST path and PATCH path
  each subset the union to their own body when writing.
- Sub-method `post_allowlist(content_type) -> frozenset[str]` and
  `patch_allowlist(content_type) -> frozenset[str]` for the import
  side's per-verb subsetting.
- Method `read_only_fields(content_type) -> frozenset[str]` for the
  complement (fields in GET response but in neither POST nor
  PATCH), used by audit logs.
- Exposes a class-level `dump_allowlists(path)` writing per-content-type
  allowlists into a debug artefact (handy when reviewing snapshot diffs).

**Testing:** unit test in `tests/unit/test_openapi_write_allowlist.py`.
Confirm `id`, `url`, `display`, `created`, `last_updated` end up in
`read_only_fields("dcim.device")`. Confirm `name`, `site`, `role`
end up in `write_allowlist("dcim.device")`.

**Estimated Effort:** 1-2h

### [FEAT-03a] Content type cache class with fetch and lookups

**Context:** `docs/frictions/02-content-types-and-generic-fks.md`.
Both source and destination need a `(app, model) ↔ id` map.

**Requirements:**

- `src/nbsnap/schema/content_types.py` with `class ContentTypeCache`.
- **`ContentTypeCache.fetch(http) -> ContentTypeCache` probes both
  endpoints (per Q13 burndown).** First tries
  `extras/content-types/`. On HTTP 404, falls back to
  `extras/object-types/`. Caches the working path on the cache
  instance as `endpoint_used`. Both endpoints honour `?limit=0` for
  this one resource.
- `id_for(app, model) -> int` raising `KeyError` on miss.
- `natural_for(id) -> tuple[str, str]` raising `KeyError` on miss.
- `has(app, model) -> bool` for soft checks.
- Read-only after `fetch`, no mutation methods.
- Iterable over `(app, model, id)` triples for diagnostics.

**Testing:** unit test in `tests/unit/test_content_type_cache.py`
with a fixture mapping. Confirm round-trip lookups and that
`id_for("zzz", "zzz")` raises `KeyError`. Integration test
`tests/integration/test_content_types.py` fetches against the source
stack, confirms standard NetBox content types (`dcim.device`,
`ipam.prefix`) are present and the ids are positive integers.

**Estimated Effort:** 1-2h

### [FEAT-03b] Content type diff helper for source vs destination

**Context:** pre-flight (`FEAT-18`) needs a "what is missing" report.

**Requirements:**

- Method `ContentTypeCache.diff(other: ContentTypeCache) ->
  ContentTypeDelta`.
- `ContentTypeDelta` dataclass: `only_on_source: set[tuple[str, str]]`,
  `only_on_destination: set[tuple[str, str]]`, `common: set[tuple[str, str]]`.
- Method `format_for_operator(self) -> str` rendering a tidy ASCII
  table for the run summary.

**Testing:** unit test in `tests/unit/test_content_type_diff.py` with
two synthetic caches: source has `{dcim.device, netbox_bgp.bgpsession}`,
destination has `{dcim.device, dcim.cable}`. Confirm `only_on_source`
contains `netbox_bgp.bgpsession`, `only_on_destination` contains
`dcim.cable`, `common` contains `dcim.device`.

**Estimated Effort:** 1-2h

### [FEAT-04a] Status fetcher for NetBox version and plugins

**Context:** `docs/frictions/06-netbox-version-drift.md`,
`docs/frictions/09-plugin-objects.md`. Manifest depends on this.

**Requirements:**

- `src/nbsnap/schema/status.py` with `@dataclass Status` carrying
  `netbox_version: str`, `python_version: str`, `installed_apps:
  list[str]`, `plugins: list[PluginInfo]`.
- `PluginInfo` dataclass: `name: str`, `version: str`.
- `Status.fetch(http) -> Status` invokes `GET /api/status/` and
  `GET /api/plugins/` (latter may be empty).
- Tolerate missing fields in older NetBoxes (`get`, not bracket index).

**Testing:** unit test in `tests/unit/test_status.py` mocking the two
endpoints with a fixture body. Confirm the dataclass is populated.
Integration test against the source stack, assert
`status.netbox_version.startswith("4.6")`.

**Estimated Effort:** 1-2h

### [FEAT-04b] version_skew comparator

**Context:** `--max-version-skew` enforcement in `FEAT-25` depends on
this.

**Requirements:**

- Method `Status.version_skew(other: Status) -> VersionSkew` returning
  an enum `NONE | PATCH | MINOR | MAJOR`.
- Parse semver-style `MAJOR.MINOR.PATCH` strings, tolerate trailing
  pre-release labels.
- Helper `parse_version(s: str) -> tuple[int, int, int]`.
- Method `skew_allowed_by(self, tolerance: VersionSkew) -> bool` for
  the policy check.

**Testing:** unit test in `tests/unit/test_version_skew.py` covering
`(4.6.2, 4.6.2) -> NONE`, `(4.6.2, 4.6.5) -> PATCH`,
`(4.6.2, 4.7.0) -> MINOR`, `(4.6.2, 5.0.0) -> MAJOR`. Confirm
`skew_allowed_by(MINOR)` accepts MINOR and below, rejects MAJOR.

**Estimated Effort:** 1-2h

### [TEST-01a] Integration schema fetch timing assertion

**Context:** `PLAN.md` Phase 1 exit criterion, under 30 seconds.

**Requirements:**

- `tests/integration/test_schema_timing.py` using the netbox-docker
  stack fixture.
- Fetch OpenAPI, content types, status from both stacks
  concurrently using `threading` (sync runtime, per `RES-02`).
- Assert each fetch completes in under 30 seconds.
- Skip the test if the stack is not up (`pytest.skip`).

**Testing:** run `pytest tests/integration/test_schema_timing.py -q`
against the live stack, confirm it passes on a healthy CI runner.
Verify the test skips cleanly when the stack is down.

**Estimated Effort:** 1-2h

### [TEST-01b] Integration content-type drift assertion

**Context:** `docs/frictions/02` M9. The two stacks must have
different content type ids for the same `(app, model)` so the
translation path is exercised.

**Requirements:**

- `tests/integration/test_content_type_drift.py`.
- Fetch content types from both stacks.
- **Informational not gating per Q14 burndown.** When
  `source.id_for("dcim", "device") != dest.id_for("dcim", "device")`,
  log INFO "content-type ids diverge between stacks, translation
  path is exercised". When the ids happen to match (two
  freshly-installed NetBoxes can land on the same sequence), log
  INFO "content-type ids match, translation path runs
  transparently" and pass.
- Both branches still exercise the ContentType translation code
  during the round-trip in `TEST-04` and later, so the
  informational assertion is enough to confirm the path is wired.

**Testing:** run the test against a fresh stack pair, confirm green
regardless of whether ids match. Inspect the run log for the
informational line so the CI summary tells the operator which
branch fired.

**Estimated Effort:** 1-2h

---

## Open, Phase 2, Graph construction and planning

### [FEAT-05a] Graph node and edge dataclasses

**Context:** `docs/03-dependency-graph.md`. Shape comes first, then
the build logic in `FEAT-05b`.

**Requirements:**

- `src/nbsnap/graph/model.py` with `@dataclass(frozen=True) Node`
  (`content_type: str`).
- `@dataclass(frozen=True) Edge` (`child: str`, `parent: str`,
  `field: str`, `nullable: bool`, `required: bool`, `is_m2m: bool`,
  `polymorphic_targets: tuple[str, ...] = ()`).
- `class Graph` with `add_node`, `add_edge`, `out_edges(node) ->
  list[Edge]`, `in_edges(node) -> list[Edge]`, `nodes() -> set[Node]`,
  `__contains__`.
- Drop an edge if either endpoint is not a node already.

**Testing:** unit test in `tests/unit/test_graph_model.py`. Build a
small graph by hand, confirm `out_edges` and `in_edges` are
symmetric. Confirm an edge added before its endpoints is rejected
(or auto-creates nodes, pick one and assert).

**Estimated Effort:** 1-2h

### [FEAT-05b] Build child-to-parent FK edges from OpenAPI

**Context:** the core of graph construction. Uses `FEAT-02c`
`field_spec`.

**Requirements:**

- `src/nbsnap/graph/build.py` with `Graph.from_openapi(openapi, scope:
  set[str]) -> Graph`.
- Iterate every content type in `scope`.
- For each field that is an FK (`field_spec.fk_target is not None`),
  add an edge `(field_spec.fk_target, content_type, field, nullable,
  required, is_m2m)`.
- Skip edges into out-of-scope content types.
- Carry the field's `is_m2m` and `nullable` faithfully.
- Method `Graph.from_openapi` deterministic, sorts content types
  before iterating so the resulting graph is reproducible.

**Testing:** unit test in `tests/unit/test_graph_build.py` with a
fixture OpenAPI containing `dcim.device`, `dcim.site`, `ipam.ipaddress`
and the relevant FKs. Assert the device-to-site edge exists, the
device-to-ipaddress edge exists with `nullable=True`. Confirm an
out-of-scope content type (e.g. `extras.objectchange`) is dropped.

**Estimated Effort:** 1-2h

### [FEAT-05c1] Polymorphic target discovery via OPTIONS request

**Context:** generic FKs (Cable terminations,
IPAddress.assigned_object, Service.assigned_object, WirelessLink
endpoints) need to know the union of legal target content types.
The primary discovery path is an `OPTIONS` request to the owning
endpoint, which NetBox surfaces with a `choices` block in the
response body. `OPTIONS` is read-only and safe against the source.

**Requirements:**

- Create `src/nbsnap/graph/polymorphic.py` with
  `discover_via_options(http, endpoint, field) -> list[str] | None`.
- Issue `OPTIONS /api/<endpoint>/`. Parse the JSON body for
  `actions.POST.<field>.choices` (NetBox 4.x convention). Each
  choice is a `{value: "<app>.<model>", display_name: "..."}` dict.
- Return the list of `value` strings sorted, or `None` when the
  endpoint has no `choices` block on that field.
- Tolerate `OPTIONS` returning `405` or `404` on plugins that omit
  the verb, return `None` in that case.
- Use the HTTP client's `OPTIONS` route from `READ_ONLY_VERBS`
  (per `FEAT-01g1`), the guard rail allows it.

**Testing:** unit test in
`tests/unit/test_polymorphic_options.py`. Mock `OPTIONS` returning
a body with `actions.POST.assigned_object_type.choices` populated,
assert the function returns the sorted list of `value` strings.
Mock `OPTIONS` returning `405`, assert `None`. Mock the body
without a `choices` block, assert `None`.

**Estimated Effort:** 1-2h

### [FEAT-05c2] Destination-only POST fallback for polymorphic target discovery

**Context:** when `OPTIONS` does not surface the choices,
`FEAT-05c1` returns `None` and we fall back to a dry-run `POST`
against the **destination only**. The fallback sends a
deliberately invalid `<field>_type` value, NetBox returns a
validation error body listing the legal types.

**Requirements:**

- Extend `src/nbsnap/graph/polymorphic.py` with
  `discover_via_post_probe(http, endpoint, field) -> list[str]`.
- Raise `PlannerRequiresDestination` immediately when
  `http.is_source() is True`. Source-bound clients cannot probe
  via writes per the production-read-only constraint.
- Send `POST /api/<endpoint>/` with `{<field>: "invalid.invalid",
  ...other_required_fields: "stub"}`. NetBox responds with a
  validation error body of the form
  `{<field>: ["Choice 'invalid.invalid' is not valid. Choices are
  'dcim.interface', 'virtualization.vminterface'."]}`.
- Parse the error string with a regex to extract the legal choices.
- Return the sorted list.
- Raise `DiscoveryFailed` when the response is not a validation
  error or the parser cannot extract choices.

**Testing:** unit test in
`tests/unit/test_polymorphic_post_probe.py`. Mock `is_source`
returning `True`, confirm `PlannerRequiresDestination` raises
without any HTTP call. Mock the destination response with a
validation error body listing two choices, confirm the parser
returns them sorted. Mock an unexpected error body, confirm
`DiscoveryFailed`.

**Estimated Effort:** 1-2h

### [FEAT-05c3] Polymorphic edge emission and cache integration

**Context:** with `FEAT-05c1` and `FEAT-05c2` in place, the graph
builder needs to emit one `Edge` per polymorphic `(child, parent)`
pair and cache the discovered target list across runs.

**Requirements:**

- Extend `src/nbsnap/graph/build.py` to detect the `<field>_type` /
  `<field>_id` pair shape on the input schema.
- For each detected polymorphic field, call
  `discover_via_options` first. If `None`, call
  `discover_via_post_probe` (which raises against source).
- Emit one `Edge(child, parent, field, nullable, required,
  is_m2m=False, polymorphic_targets=tuple(targets))` per
  `(child, parent)` pair.
- Only the `<field>_id` field contributes to edges, the
  `<field>_type` is metadata.
- Cache the discovered target lists per `(content_type, field)`
  in `<snapshot>/polymorphic-targets.json`. Load the cache on
  startup unless `--refresh-schema` is passed. Cache file format,
  `{"<content_type>.<field>": ["target1", "target2"]}`.

**Testing:** unit test in
`tests/unit/test_graph_polymorphic_emission.py`. With a fixture
schema for `ipam.ipaddress` whose `assigned_object_type` resolves
(via mocked discovery) to `["dcim.interface",
"virtualization.vminterface"]`, run the builder and confirm two
edges exist with the same `field` and different `parent`,
`polymorphic_targets` populated. Confirm the cache file is written
and re-loading the builder reads from the cache without invoking
the discovery functions.

**Estimated Effort:** 1-2h

### [FEAT-06a] Hand-rolled Tarjan SCC implementation

**Context:** `docs/03-dependency-graph.md` "Cycle detection, full
algorithm". Hand-rolled per `RES-04`.

**Requirements:**

- `src/nbsnap/graph/scc.py` with `tarjan_scc(graph: Graph) ->
  list[list[str]]`.
- Standard iterative Tarjan (avoid recursion to survive deep
  pseudo-paths in plugin-heavy schemas).
- SCCs of size 1 with no self-loop are dropped from the output.
- Stable ordering: SCCs sorted by their smallest-content-type member.

**Testing:** unit test in `tests/unit/test_graph_scc.py` covering
five cases. Case 1, no cycles, empty result. Case 2, single 3-node
cycle, one SCC of size 3. Case 3, two disjoint cycles, two SCCs.
Case 4, self-loop, one SCC of size 1. Case 5, the Device/IPAddress/
Interface fixture from `docs/03-dependency-graph.md`, one SCC of
size 3.

**Estimated Effort:** 1-2h

### [FEAT-06b] Deferred-edge selection within an SCC

**Context:** `docs/03-dependency-graph.md`. Preference order:
`(nullable=True, required=False)` > `(nullable=True, required=True)` >
everything else. Tie-break by source-out-degree.

**Requirements:**

- `src/nbsnap/graph/plan.py` with
  `select_deferred_edges(graph, sccs) -> list[Edge]`.
- For each SCC, collect every internal edge.
- Pick the deferral edge per the priority rule.
- Tie-break by `len(graph.out_edges(edge.child))`, larger wins.
- Raise `NoNullableEdgeInSCC` if no nullable edge exists (operator
  data error, surfaces clearly).

**Testing:** unit test in `tests/unit/test_deferred_selection.py`.
Case 1, SCC with one nullable+not-required edge, that edge is
selected. Case 2, two nullable+not-required edges, the one with the
higher-out-degree source wins. Case 3, only non-nullable edges,
expect `NoNullableEdgeInSCC`.

**Estimated Effort:** 1-2h

### [FEAT-06c] Final topo sort via graphlib

**Context:** after deferred edges are removed, the graph is acyclic
and `graphlib.TopologicalSorter` does the rest.

**Requirements:**

- `src/nbsnap/graph/plan.py` `class Plan` with `write_order: list[str]`,
  `deferred_edges: list[Edge]`.
- `Plan.from_graph(graph) -> Plan` runs Tarjan, picks deferred
  edges, removes them from a graph copy, feeds the rest into
  `graphlib.TopologicalSorter`, captures the resulting order.
- Stable: ties in topo order broken by content-type alphabetical sort.
- Method `Plan.to_json() -> dict` for `nbsnap plan` output and
  manifest embedding.

**Testing:** unit test in `tests/unit/test_plan.py` building a graph
from the OpenAPI fixture, running `Plan.from_graph`, confirming
`write_order` is acyclic in the residual graph and the deferred
edges list contains `Device.primary_ip4`. Run the same plan twice,
confirm byte-identical `to_json()` output.

**Estimated Effort:** 1-2h

### [RES-04] Decide NetworkX vs hand-rolled Tarjan

**Context:** `FEAT-06a` is hand-rolled per the design doc preference,
but recording the decision keeps the option open.

**Requirements:**

- Author `docs/implementation/04-graph-lib.md`.
- Compare NetworkX (battle-tested, ~10MB dep) vs hand-rolled (~60
  lines, no dep).
- Recommend hand-rolled. Name the trigger that would flip the choice
  (an SCC bug we cannot reproduce, or a graph algorithm we suddenly
  need beyond Tarjan).
- Link from `docs/03-dependency-graph.md` and from `FEAT-06a`.

**Testing:** self-review the doc, confirm the trigger condition is
testable. Run `pip install -e .` and confirm `networkx` is not in
the resolved deps.

**Estimated Effort:** 1-2h

### [FEAT-07a] Wire nbsnap plan sub-command to the planner

**Context:** `INFRA-02b` stubbed the sub-command, this lights it up
end to end.

**Requirements:**

- `src/nbsnap/graph/cli.py` with `run_plan(args) -> int`.
- Accepts `--url`, `--token`, `--scope` (path to a JSON file listing
  in-scope content types, defaults to the renderer-minimum set from
  `docs/02-data-model-scope.md`).
- Fetches OpenAPI, builds the graph, runs the planner, prints
  `Plan.to_json()` to stdout, returns 0.
- Replaces the stub in `cli.py`.

**Testing:** integration test in `tests/integration/test_plan_cli.py`.
Run `nbsnap plan --url <source> --token <source-token>` via
`subprocess.run`, capture stdout, parse as JSON, confirm
`write_order` is a non-empty list and contains `dcim.site` before
`dcim.device`.

**Estimated Effort:** 1-2h

### [FEAT-07b] Human-readable plan output to stderr

**Context:** operators reading the run interactively want a tidy
table.

**Requirements:**

- Extend `run_plan` with a `--format {json, table}` flag, default
  `json` for scripts, `table` when stdout is a TTY.
- Table form: two columns, "phase 1 write order" and "phase 2
  deferred edges", each row a content type and the deferred edges
  alongside.
- Reuse the `rich.table.Table` import only if `rich` is a runtime
  dep (decided in `FEAT-29`), otherwise hand-roll ASCII.

**Testing:** unit test in `tests/unit/test_plan_format.py`. Pipe a
hand-built `Plan` through the table renderer, confirm the result is
non-empty plain text. Run `nbsnap plan --format table` against the
source stack, eyeball the output.

**Estimated Effort:** 1-2h

### [TEST-02a] Unit test, cycle detection on the canonical 3-node cycle

**Context:** the Device/IPAddress/Interface case from
`docs/03-dependency-graph.md` "Worked example".

**Requirements:**

- `tests/unit/test_plan_canonical_cycle.py`.
- Build a `Graph` by hand with three nodes and three edges matching
  the worked example.
- Run `Plan.from_graph`.
- Assert `deferred_edges` contains exactly one edge,
  `child=dcim.device`, `field=primary_ip4`.
- Assert `write_order` after removing the deferred edge is a valid
  topological order (assert the index of `dcim.device` is before
  `dcim.ipaddress` is false because of the deferral, so assert in
  the residual graph).

**Testing:** the unit test itself is the testing step. Run
`pytest tests/unit/test_plan_canonical_cycle.py -q`, confirm green.

**Estimated Effort:** 1-2h

### [TEST-02b] Unit test, multi-SCC and pseudo-cycles

**Context:** the planner must handle two disjoint SCCs, a 1-node
self-loop, and a non-cyclic node correctly in one run.

**Requirements:**

- `tests/unit/test_plan_multi_scc.py`.
- Build a graph with: SCC A (3 nodes, all nullable cycle-breakers),
  SCC B (2 nodes, one nullable cycle-breaker), one self-loop node,
  one acyclic linear chain of 3 nodes.
- Run `Plan.from_graph`.
- Assert exactly 3 deferred edges (one per SCC including the
  self-loop).
- Assert the linear chain is fully topo-sorted and self-consistent.

**Testing:** `pytest tests/unit/test_plan_multi_scc.py -q`, confirm
green. Run twice in a row, assert the deferred-edge list is
byte-identical (stable ordering).

**Estimated Effort:** 1-2h

---

## Open, Phase 3, Natural key system

### [FEAT-08a] NKSpec and NKField dataclasses

**Context:** `docs/02-data-model-scope.md` table, the spec is the
shared source of truth between exporter and importer.

**Requirements:**

- `src/nbsnap/natkey/spec.py` with `@dataclass(frozen=True) NKField`
  (`name: str`, `nullable: bool = False`).
- `@dataclass(frozen=True) NKSpec` (`content_type: str`, `strategy:
  Literal["slug", "composite", "polymorphic-set"]`, `fields:
  tuple[NKField, ...]`, `parent_ct: str | None = None`).
- Module-level `DEFAULT_SPECS: dict[str, NKSpec]` empty for now,
  populated in `FEAT-08b`.
- `lookup(content_type: str, *, specs=DEFAULT_SPECS) -> NKSpec`
  raising `UnknownNK`.

**Testing:** unit test in `tests/unit/test_nk_spec_shape.py`. Build a
sample `NKSpec` by hand, confirm it is hashable (frozen), confirm
`lookup("dcim.device")` raises until populated, confirm `lookup`
with a custom specs dict returns the right one.

**Estimated Effort:** 1-2h

### [FEAT-08b1] DCIM NKSpec entries (Site through Cable)

**Context:** the DCIM half of `DEFAULT_SPECS`. Covers the physical
topology and the L1/L2 components. Natural-key shapes come from
`docs/02-data-model-scope.md`, polymorphic-set for Cable per Q16.

**Requirements:**

- Extend `src/nbsnap/natkey/spec.py` with `DEFAULT_SPECS` entries
  for DCIM content types,
  `dcim.site` (slug),
  `dcim.location` (composite `(site.slug, slug)`),
  `dcim.rack` (composite `(site.slug, name)`),
  `dcim.manufacturer` (slug),
  `dcim.devicetype` (composite `(manufacturer.slug, slug)`),
  `dcim.platform` (slug),
  `dcim.devicerole` (slug),
  `dcim.device` (composite `(site.slug, name)`),
  `dcim.interface` (composite `(device.{key}, name)`),
  `dcim.cable` (polymorphic-set),
  `dcim.virtualchassis` (composite `(domain, name)`).
- One `NKSpec` instance per content type, frozen dataclass per
  `FEAT-08a`.
- Each entry carries a `# docs/02-data-model-scope.md row N` source
  comment so reviewers can trace the choice.

**Testing:** unit test in `tests/unit/test_nk_default_specs_dcim.py`.
Iterate every DCIM content type listed in the in-scope set from
Q16, assert it has an entry in `DEFAULT_SPECS`. Assert
`DEFAULT_SPECS["dcim.device"].fields[0].name == "site.slug"`.
Assert `dcim.cable` uses `polymorphic-set`.

**Estimated Effort:** 1-2h

### [FEAT-08b2] DCIM port and inventory NKSpec entries plus all IPAM NKSpecs

**Context:** the second batch of `DEFAULT_SPECS` entries. Covers
the DCIM port families and the full IPAM addressing model. All
entries follow the composite or slug strategies.

**Requirements:**

- Extend `DEFAULT_SPECS` with DCIM port families,
  `dcim.powerport`, `dcim.poweroutlet`, `dcim.powerfeed`,
  `dcim.powerpanel`, `dcim.frontport`, `dcim.rearport`,
  `dcim.consoleport`, `dcim.consoleserverport`,
  `dcim.inventoryitem`. All composite `(device.{key}, name)`.
- Extend `DEFAULT_SPECS` with IPAM,
  `ipam.vlangroup` (slug),
  `ipam.vlan` (composite `(vlangroup.slug or site.slug, vid)`),
  `ipam.aggregate` (`prefix`),
  `ipam.prefix` (composite `(prefix, vrf?, tenant?)`),
  `ipam.iprange` (composite `(start_address, end_address, vrf?)`),
  `ipam.ipaddress` (polymorphic-set, composite
  `(address, vrf?, assigned_object?)`),
  `ipam.role` (slug),
  `ipam.rir` (slug),
  `ipam.vrf` (`rd or name`),
  `ipam.routetarget` (`name`),
  `ipam.asn` (`asn`),
  `ipam.asnrange` (slug),
  `ipam.fhrpgroup` (composite `(protocol, group_id)`),
  `ipam.service` (composite
  `(device.{key} or virtual_machine.{key}, name, ports)`).

**Testing:** unit test in
`tests/unit/test_nk_default_specs_ipam_ports.py`. Iterate the DCIM
port families plus every IPAM content type from Q16, assert each
has a `DEFAULT_SPECS` entry. Assert `ipam.ipaddress` uses
`polymorphic-set`. Assert
`DEFAULT_SPECS["ipam.vlan"].strategy == "composite"`.

**Estimated Effort:** 1-2h

### [FEAT-08b3] Tenancy and decorating Extras NKSpec entries

**Context:** the smaller third batch covers Tenancy (per Q16,
in-scope) and decorating Extras (CFs, ChoiceSets, Tags whose
`object_types` decorates the in-scope set).

**Requirements:**

- Extend `DEFAULT_SPECS` with Tenancy,
  `tenancy.tenant` (slug),
  `tenancy.tenantgroup` (slug). Contacts and ContactAssignments
  stay out per Q16.
- Extend `DEFAULT_SPECS` with decorating Extras,
  `extras.customfield` (`name`),
  `extras.customfieldchoiceset` (`name`),
  `extras.tag` (slug).
- Document in a top-of-file comment that the exporter filters CF
  and Tag rows whose `object_types` does not intersect the
  in-scope content type set (per Q22), the NKSpec entries here are
  the spec shapes only, not the filter logic.

**Testing:** unit test in
`tests/unit/test_nk_default_specs_tenancy_extras.py`. Assert each
of the five content types above has a `DEFAULT_SPECS` entry.
Assert `tenancy.contact` and `tenancy.contactassignment` are
**not** in `DEFAULT_SPECS` (out of scope per Q16). Assert the
full in-scope set from Q16 is now fully populated across `08b1`,
`08b2`, and `08b3` combined.

**Estimated Effort:** 1-2h

### [FEAT-09a] Slug-strategy resolver

**Context:** the simplest of the three strategies, covers Site,
Location, Manufacturer, and friends.

**Requirements:**

- `src/nbsnap/natkey/resolve.py` with `resolve(record, content_type,
  *, specs=DEFAULT_SPECS) -> tuple`.
- Dispatch on `spec.strategy`. Handle `"slug"` here: return
  `(record["slug"],)`.
- Raise `UnresolvedNK` if `record` is missing the slug field.

**Testing:** unit test in `tests/unit/test_nk_slug.py`. Confirm
`resolve({"slug": "hall-d"}, "dcim.site") == ("hall-d",)`. Confirm
missing slug raises `UnresolvedNK`.

**Estimated Effort:** 1-2h

### [FEAT-09b] Composite-strategy resolver with nested parent recursion

**Context:** Device, Interface, Rack, Prefix, IPAddress all use
composite keys including a parent FK whose own NK needs resolving.

**Requirements:**

- Add `"composite"` branch to `resolve`.
- For each `NKField`, walk dotted paths like `"site.slug"` against
  the record.
- If a field value is itself a nested object (e.g. `record["site"]
  == {"slug": "hall-d"}`), recurse into the parent spec to build
  the nested NK.
- If a parent FK is null and the field is `nullable`, emit `None` for
  that position rather than failing.
- Returns a tuple where parent NKs are nested tuples.

**Testing:** unit test in `tests/unit/test_nk_composite.py`. Confirm
Device resolution: `resolve({"name": "D39A", "site": {"slug":
"hall-d"}}, "dcim.device") == (("hall-d",), "D39A")`. Confirm
Interface resolution: `resolve({"name": "Vlan600", "device": {"name":
"D39A", "site": {"slug": "hall-d"}}}, "dcim.interface")` produces a
fully-nested tuple. Confirm a missing nullable VRF on `ipam.prefix`
emits `None`.

**Estimated Effort:** 1-2h

### [FEAT-09c] Polymorphic-set resolver for Cable terminations

**Context:** `docs/frictions/05-cable-termination-model.md` M1, the
cable is keyed by the sorted set of its termination NKs.

**Requirements:**

- Add `"polymorphic-set"` branch to `resolve`.
- For Cables, walk `a_terminations` and `b_terminations`, resolve
  each termination by its `object_type` + nested `object` block.
- Use `lookup(object_type)` to find the termination's own spec.
- Build a `frozenset` of `(object_type, nested_nk)` tuples, then
  convert to a sorted tuple of tuples.
- Generalise for `ipam.ipaddress.assigned_object` (the resolver
  inspects `assigned_object_type` and `assigned_object` then
  recurses).

**Testing:** unit test in `tests/unit/test_nk_polymorphic.py`. Build
a Cable record with two interface terminations, confirm
`resolve(...)` returns a sorted tuple of two
`("dcim.interface", (...))` entries. Build the same Cable with the A
and B sides swapped, confirm the NK is identical (sort is order
independent).

**Estimated Effort:** 1-2h

### [FEAT-10a] Duplicate-NK audit walker

**Context:** `docs/frictions/04` M6. Walks every in-scope content
type, resolves NK for every record, reports duplicates.

**Requirements:**

- `src/nbsnap/natkey/audit.py` with `audit(http, specs=DEFAULT_SPECS)
  -> AuditReport`.
- `AuditReport` dataclass: `duplicates: dict[str, list[tuple[NK,
  list[int]]]]`, `unresolvable: dict[str, list[int]]`.
- For each content type in `specs`, iterate `http.get_all` over the
  corresponding endpoint, resolve NK, group by NK, surface groups of
  size >1.
- `format_for_operator(self) -> str` returning a tidy summary.

**Testing:** integration test in `tests/integration/test_natkey_audit.py`
against the source stack post-seed. Confirm the report is empty (no
duplicates) for the seeded data. Add a deliberately duplicate device
via API, re-run, confirm it appears in `duplicates["dcim.device"]`.

**Estimated Effort:** 1-2h

### [FEAT-10b] Wire nbsnap verify-natkeys CLI sub-command

**Context:** `INFRA-02b` stubbed the sub-command, this fills it in.

**Requirements:**

- `src/nbsnap/natkey/cli.py` with `run_verify_natkeys(args) -> int`.
- Accepts `--url`, `--token`, exit 0 on clean report, exit 1 on any
  duplicate.
- Print `report.format_for_operator()` to stderr, JSON to stdout if
  `--format json`.
- Replace the stub in `cli.py`.

**Testing:** integration test in `tests/integration/test_verify_natkeys_cli.py`.
Run against the source stack, confirm exit 0. Seed a duplicate,
re-run, confirm exit 1 and the JSON output mentions the duplicate.

**Estimated Effort:** 1-2h

### [TEST-03a] Unit tests, slug-strategy round-trip

**Context:** every slug-keyed content type from `FEAT-08b`.

**Requirements:**

- `tests/unit/test_nk_slug_roundtrip.py`.
- For each slug-keyed content type (`dcim.site`, `dcim.devicerole`,
  ...), build two equivalent fixtures, assert resolve produces the
  same NK. Build two non-equivalent fixtures, assert different NKs.
- Assert NK is JSON-serialisable (tuple of strings).

**Testing:** `pytest tests/unit/test_nk_slug_roundtrip.py -q`,
confirm green. Add a brand-new content type to the spec, confirm the
parameterised test picks it up automatically.

**Estimated Effort:** 1-2h

### [TEST-03b] Unit tests, composite and polymorphic-set strategies

**Context:** Device, Interface, IPAddress, Cable.

**Requirements:**

- `tests/unit/test_nk_composite_roundtrip.py`.
- Same-NK and different-NK pairs for `dcim.device`, `dcim.interface`,
  `dcim.rack`, `ipam.prefix`, `ipam.ipaddress`, `dcim.cable`.
- Assert JSON-serialisability via `json.dumps` after a recursive
  list-conversion helper.
- Order-independence assertion for `dcim.cable` (A and B swapped
  produce identical NK).

**Testing:** `pytest tests/unit/test_nk_composite_roundtrip.py -q`,
confirm green. Mutate a single non-NK field on a Device fixture,
confirm the NK does not change.

**Estimated Effort:** 1-2h

---

## Open, Phase 4, Export engine

### [FEAT-11a] Per-endpoint extractor function shape

**Context:** `docs/05-export-import-workflow.md` Phase E2. The
extractor is the inner loop, fed by `FEAT-12` to `FEAT-14`.

**Requirements:**

- `src/nbsnap/export/extract.py` with `extract(http, content_type,
  ctx) -> Iterator[dict]`.
- `ctx: ExtractContext` dataclass carrying `openapi`, `nk_resolver`,
  `install_local`, `parent_id_cache: dict[str, dict[int, tuple]]`.
- Resolve the API endpoint from `content_type` via a known mapping
  (`dcim.device -> dcim/devices/`).
- Use `http.get_all` to iterate.
- For each row, populate `parent_id_cache[content_type][row["id"]] =
  resolved_nk`.
- Yield a dict with `_nk`, `_op = "upsert"`, and the rest of the row
  unmodified (filtering and rewriting come in `FEAT-11b` and `FEAT-11c`).

**Testing:** unit test in `tests/unit/test_extract_shape.py` mocking
`http.get_all` to yield two `dcim.site` rows. Confirm the extractor
yields two dicts, each with `_nk` populated and `_op == "upsert"`.

**Estimated Effort:** 1-2h

### [FEAT-11b] Field allowlist filter step

**Context:** `docs/02-data-model-scope.md` "Field-level allowlist
policy" plus `FEAT-02d`.

**Requirements:**

- Extend `extract` to drop fields not in `ctx.openapi.write_allowlist(
  content_type)`.
- Always retain `_nk` and `_op`.
- Always retain `custom_fields` regardless of allowlist (custom
  values land via a separate sub-schema).
- Emit one INFO log line per dropped field per content type, once
  per run (deduplicated by `(content_type, field)`).

**Testing:** unit test in `tests/unit/test_extract_filter.py` with a
fixture row containing `id`, `url`, `display`, `name`, `slug`,
`custom_fields`. Confirm `id`, `url`, `display` are dropped, `name`,
`slug`, `custom_fields` are kept. Confirm the log line emits exactly
once across two rows.

**Estimated Effort:** 1-2h

### [FEAT-11c] FK rewrite integration into extractor

**Context:** wires the FK rewriter from `FEAT-12` into the per-row
processing loop.

**Requirements:**

- Call `rewrite_fks(row, content_type, openapi=ctx.openapi,
  resolve_parent=ctx.parent_lookup)` per row.
- `ctx.parent_lookup(content_type, source_id) -> tuple | None` looks
  up the parent NK from `parent_id_cache`.
- If a parent NK is not yet cached, raise `ParentNotYetWalked`. The
  caller (`FEAT-22a`) should sort content types by topo order to
  avoid this.

**Testing:** unit test in `tests/unit/test_extract_fk_rewrite.py`. A
Device row points at site id 7, the cache has `(dcim.site, 7) ->
("hall-d",)`. Confirm the extractor produces a row with
`site = {"_ref": "dcim.site", "_nk": ["hall-d"]}`. Confirm a missing
parent raises `ParentNotYetWalked`.

**Estimated Effort:** 1-2h

### [FEAT-11d] Install-local classifier integration into extractor

**Context:** wires `FEAT-13` into the extractor and records findings
in the flag file.

**Requirements:**

- For each field in the (post-allowlist, post-FK-rewrite) row, call
  `ctx.install_local.classify(content_type, field, value)`.
- If a classification returns a `Reason`, emit a record into
  `ctx.flag_writer` (one per finding).
- Do not strip the value, the operator decides on import (per
  `docs/frictions/08` M1).

**Testing:** unit test in `tests/unit/test_extract_install_local.py`.
Feed a Webhook row with `payload_url = "https://salt.infra.glitched.se/hook"`.
Confirm the row is still yielded with the URL intact, and a flag
record is emitted with `reason = "rfc1918-or-internal-hostname"`.

**Estimated Effort:** 1-2h

### [FEAT-12a] FK rewriter for simple FKs

**Context:** `docs/frictions/02` and `04`. Single-id FK fields are
the common case.

**Requirements:**

- `src/nbsnap/export/fk_rewrite.py` with `rewrite_fks(record,
  content_type, *, openapi, resolve_parent) -> dict`.
- For each field where `openapi.field_spec` reports `fk_target` and
  not `is_m2m` and not polymorphic, replace the value with `{"_ref":
  fk_target, "_nk": resolve_parent(fk_target, value["id"])}`.
- Tolerate the FK already being nested (NetBox sometimes inlines the
  parent record, sometimes returns just the id).

**Testing:** unit test in `tests/unit/test_fk_rewrite_simple.py`.
Run on a Device row pointing at site id 7 (inlined as `{"id": 7,
"slug": "hall-d"}`). Confirm the rewritten value is `{"_ref":
"dcim.site", "_nk": ["hall-d"]}`. Repeat with a bare integer site id
and the cache populated, confirm the same output.

**Estimated Effort:** 1-2h

### [FEAT-12b] FK rewriter for M2M lists

**Context:** `Tag.object_types`, `Interface.tagged_vlans`, and
similar.

**Requirements:**

- Extend `rewrite_fks` for `is_m2m=True`.
- Iterate the list, rewrite each element, return a list of `{"_ref":
  ..., "_nk": ...}` dicts.
- **Sort the result by `_nk` tuple before returning (per Q18
  burndown).** NetBox stores M2M as a set, sort guarantees stable
  output across runs and matches the upsert equality posture from
  `FEAT-20b` / `FEAT-21b`. The sort is stable Python `sorted()` on
  the natural-key tuple itself, no library needed.

**Testing:** unit test in `tests/unit/test_fk_rewrite_m2m.py`. Run
on an Interface row with `tagged_vlans = [{"id": 5}, {"id": 3}]`,
cache populated with `(ipam.vlan, 3) -> (..., 100)` and
`(ipam.vlan, 5) -> (..., 200)`. Confirm the output is sorted (the
`(..., 100)` entry comes first).

**Estimated Effort:** 1-2h

### [FEAT-12c] FK rewriter for polymorphic generic FKs

**Context:** `IPAddress.assigned_object`, `ContactAssignment.object`,
`Cable termination.object`.

**Requirements:**

- Extend `rewrite_fks` for edges with `polymorphic_targets` set.
- Read `<field>_type` to find the target content type, then resolve
  the target's NK via the cache for that content type.
- Emit `{"_ref": target_ct, "_nk": resolved_nk}`.
- Remove the `<field>_type` and `<field>_id` raw fields after
  rewriting (replaced by the single composite field).

**Testing:** unit test in `tests/unit/test_fk_rewrite_polymorphic.py`.
Run on an IPAddress row with `assigned_object_type = "dcim.interface"`
and `assigned_object_id = 42`, cache populated. Confirm the rewritten
row carries `assigned_object = {"_ref": "dcim.interface", "_nk": [...]}`
and `assigned_object_type`/`assigned_object_id` are gone.

**Estimated Effort:** 1-2h

### [FEAT-13a] Install-local rule engine, narrowed to source-host matching

**Context:** under the network-only scope (Q16), webhook URLs,
data sources, custom links, and saved filters are all out of scope.
The only install-local risk remaining is `IPAddress.dns_name` that
points at the source NetBox itself. Per Q17, the classifier
narrows to a single rule.

**Requirements:**

- `src/nbsnap/export/install_local.py` with `class InstallLocalClassifier`.
- `classify(content_type, field, value) -> Reason | None`.
- Single-rule `Reasons` enum, `MATCHES_SOURCE_NETBOX_HOST`. Other
  rules from the earlier draft are dropped, the network-only scope
  removes their targets.
- Operates over `IPAddress.dns_name` only. The
  `(content_type, field)` filter rejects other inputs before any
  comparison.
- Configured with the source NetBox host (read from
  `NB_SOURCE_URL` env at construction).
- Pure string comparison, no DNS resolution. `FEAT-13b` and
  `RES-05` are dropped.

**Testing:** unit test in `tests/unit/test_install_local_rules.py`.
Cases, `IPAddress.dns_name = "netbox.infra.glitched.se"` with the
source host configured to the same value matches
`MATCHES_SOURCE_NETBOX_HOST`. `IPAddress.dns_name =
"d39a.infra.glitched.se"` does not match (different host).
`(content_type="extras.webhook", field="payload_url", value=...)`
does not match (out of scope filter on the input).

**Estimated Effort:** 1-2h

### [FEAT-13c] Flag file writer

**Context:** every classification finding lands in
`<snapshot>/_flagged/install-local.jsonl` per `docs/04-snapshot-format.md`.

**Requirements:**

- `src/nbsnap/export/flag_writer.py` with `FlagWriter` context
  manager.
- `FlagWriter(path).record(kind, nk, field, value, reason)`.
- Appends one JSON line per finding.
- On exit, the file is closed atomically (write to `.tmp`, then
  `os.replace`).
- `FlagWriter.summary() -> dict[str, int]` returns per-kind counts
  for the manifest.

**Testing:** unit test in `tests/unit/test_flag_writer.py`. Open a
writer, record three findings, close. Confirm the file contains
three JSON lines in order. Confirm `summary()` aggregates correctly.

**Estimated Effort:** 1-2h

### [FEAT-14a] JSONL writer with atomic file replace

**Context:** `docs/04-snapshot-format.md`, atomic writes protect
against partial files on crash.

**Requirements:**

- `src/nbsnap/export/serialize.py` with `write_jsonl(path, records:
  Iterable[dict])`.
- Write to `<path>.tmp`, then `os.replace` to the final path.
- Use `json.dumps(sort_keys=False)` so field insertion order
  survives.
- Append a trailing newline to each record.
- Helper `dump_json(path, value)` for the manifest and similar
  single-object files.

**Testing:** unit test in `tests/unit/test_jsonl_writer.py`. Write
two records, read them back, assert equality. Simulate a crash by
raising an exception inside the iterator midway, assert no final
file exists at the target path (only the .tmp).

**Estimated Effort:** 1-2h

### [FEAT-14b] Stable sort by NK tuple

**Context:** `docs/04-snapshot-format.md` "Sort order".

**Requirements:**

- `write_jsonl` accepts an optional `sort_key=lambda r: r["_nk"]`.
- Sort the records into memory before writing (acceptable for v1, per
  type we are bounded).
- Add a helper `tuple_compare(nk_a, nk_b) -> int` for the nested
  tuple shape (a list with nested lists).
- Define behaviour when comparing types: tuples are compared
  positionally; `None` sorts before any string.

**Testing:** unit test in `tests/unit/test_jsonl_sort.py`. Three
records with NKs `["b"]`, `["a"]`, `["c"]`, confirm output is
`["a"]`, `["b"]`, `["c"]`. Nested case with NKs `[["x", "a"]]` and
`[["x", "b"]]`, confirm correct ordering.

**Estimated Effort:** 1-2h

### [FEAT-15a] Manifest dataclass

**Context:** `docs/04-snapshot-format.md` "Full manifest.json schema".

**Requirements:**

- `src/nbsnap/export/manifest.py` with `@dataclass Manifest`
  carrying every field in the schema doc.
- **Exclusions block shape per Q20 burndown.** Replace the earlier
  per-field map with a hybrid shape,
  `exclusions = {"scope": "network-only",
  "opt_in": {}, "install_local_flags_count": <int>}`. The
  `opt_in` empty object is a forward-compatible slot for any
  future `--include-X` flag that opts a category back in. No
  format-version bump required when the slot grows entries.
- `Manifest.to_dict() -> dict` for JSON serialisation.
- `Manifest.write(path)` writes `manifest.json` via `dump_json`.
- Class method `Manifest.read(path) -> Manifest` for import-side
  consumption.
- Method `Manifest.scope() -> Literal["network-only"]` for
  pre-flight comparison on the import side.

**Testing:** unit test in `tests/unit/test_manifest_io.py`. Build a
`Manifest` by hand, write, read, assert equality. Confirm
`to_dict()` carries every documented field.

**Estimated Effort:** 1-2h

### [FEAT-15b] PerfTimer context manager

**Context:** the manifest needs per-endpoint timings.

**Requirements:**

- `src/nbsnap/export/perf.py` with `class PerfTimer`.
- `with timer.measure(name): ...` records a `(name, duration)` entry.
- `timer.merge_into_manifest(manifest)` populates
  `manifest.performance.endpoint_timings`.
- Counters aggregated per name (sum, max, count).

**Testing:** unit test in `tests/unit/test_perf_timer.py`. Use
`time.monotonic` mocking, time three operations under two names,
confirm `merge_into_manifest` produces the expected structure.

**Estimated Effort:** 1-2h

### [FEAT-16a] Progress log writer

**Context:** `docs/frictions/10` M7. One checkpoint per page so a
crash recovers from the last successful page.

**Requirements:**

- `src/nbsnap/export/resume.py` with `class ProgressLog`.
- `checkpoint(endpoint, next_url)` appends a JSON line.
- File location `<snapshot>/.progress.jsonl`.
- On a clean end-of-endpoint, append a `{"endpoint": ..., "done":
  true}` marker.
- Method `clear()` removes the file at the end of a successful run.

**Testing:** unit test in `tests/unit/test_progress_writer.py`.
Checkpoint twice, then mark done, then `clear`. Confirm the file
contains the right sequence, then disappears after `clear`.

**Estimated Effort:** 1-2h

### [FEAT-16b] Resume_from reader

**Context:** counterpart to `FEAT-16a`. The exporter reads the
progress log on startup to resume the right page.

**Requirements:**

- `ProgressLog.resume_for(endpoint) -> str | None` returns the last
  `next_url` for the endpoint, or `None` if absent or `done`.
- Tolerate a partially-written last line (skip on JSON parse error).
- Integration with `FEAT-11a`: the extractor consults the log before
  calling `get_all`.

**Testing:** unit test in `tests/unit/test_progress_reader.py`.
Write a file with two endpoints, one done, one mid-way. Confirm
`resume_for(done_endpoint)` returns `None` and
`resume_for(mid_endpoint)` returns the saved URL.

**Estimated Effort:** 1-2h

### [FEAT-17a] nbsnap export CLI argument parser

**Context:** `INFRA-02b` stubbed it. This wires the flags from
`docs/05-export-import-workflow.md`.

**Requirements:**

- `src/nbsnap/export/cli.py` with `add_export_parser(subparsers)`.
- Flags (per Q21 burndown, three earlier flags dropped):
  `--url`, `--token`, `--out`, `--scrub`, `--replacement-map`,
  `--page-size`, `--max-concurrent`, `--no-verify-tls`,
  `--refresh-schema`. Drop `--include-password-hashes`,
  `--include-journal`, `--source-db-url`, `--resolve-webhook-urls`
  entirely.
- Env precedence on `--url` and `--token`: flag,
  `NB_SOURCE_URL`/`NB_SOURCE_TOKEN`, then `NB_URL`/`NB_TOKEN`.
- `--scrub` takes a comma-separated list of categories. Under
  network-only scope, the only category currently meaningful is
  `install-local-dns` (the narrowed classifier output from
  `FEAT-13a`). The flag stays for forward-compat with future
  categories.

**Testing:** unit test in `tests/unit/test_export_cli_args.py`.
Parse a representative argv vector, assert the resulting namespace.
Confirm `NB_SOURCE_TOKEN` is consumed when `--token` is omitted.
Confirm that the dropped flags
(`--include-password-hashes`, `--include-journal`,
`--source-db-url`, `--resolve-webhook-urls`) raise an
`argparse.ArgumentError` so an operator using a stale runbook gets
a clear pointer to the network-only scope change.

**Estimated Effort:** 1-2h

### [FEAT-17b] Wire export CLI to the engine and summary printer

**Context:** the parser from `FEAT-17a` invokes the extractor loop
and prints the end-of-run summary.

**Requirements:**

- `run_export(args) -> int`.
- Compose `NetboxHTTP.from_env("source", url=args.url, token=args.token)`.
- Fetch OpenAPI, content types, status, store in a build context.
- Plan via `Plan.from_graph`.
- For each content type in `plan.write_order`, run `extract`, pipe
  to `write_jsonl`.
- Emit `_deferred.jsonl` from the gathered deferred FKs.
- Write `manifest.json`.
- Print a per-type record-count summary on stderr.
- Replace the stub in `cli.py`.

**Testing:** integration test in `tests/integration/test_export_smoke.py`
against a seeded source stack. Run `nbsnap export --url --token --out
/tmp/snap`, confirm exit 0, confirm at least `dcim/sites.jsonl`,
`dcim/devices.jsonl`, `manifest.json` exist with non-trivial content.

**Estimated Effort:** 1-2h

### [TEST-04] Export reproducibility integration test

**Context:** `goals.md` success criterion 3 / `PLAN.md` Phase 4 exit.

**Requirements:**

- `tests/integration/test_export_reproducibility.py`.
- Seed source stack, run export to `/tmp/a/`, run again to `/tmp/b/`.
- Compare directory trees: every per-type `.jsonl` file must be
  byte-identical. The only allowed delta is `manifest.exported_at`
  (and any timer values).
- Use `difflib.unified_diff` to surface deltas in the assertion
  message.

**Testing:** the test itself is the testing step. Run it twice,
confirm green both times. Mutate a single Device's name on the
source stack between runs, run the test, confirm it fails with a
clear diff.

**Estimated Effort:** 1-2h

### [TEST-05] Renderer-minimum endpoint contract test

**Context:** `goals.md` success criterion 5, every endpoint marked
`M` in `docs/02-data-model-scope.md` must be hit.

**Requirements:**

- `tests/integration/test_renderer_minimum_coverage.py`.
- Monkey-patch `NetboxHTTP.get_all` to record every (method, url) tuple.
- Run `nbsnap export` against the seeded source.
- Build the expected endpoint set from the M-rows of the data-model-scope
  doc (hard-code the list, with a comment pointing back at the doc).
- Assert `recorded >= expected`.

**Testing:** run on a clean run, confirm green. Comment out the
prefix walking in the export engine, confirm the test fails and the
assertion lists the missing endpoints.

**Estimated Effort:** 1-2h

---

## Open, Phase 5, Import engine

### [FEAT-18a] Pre-flight version and format compatibility check

**Context:** `docs/05-export-import-workflow.md` Phase I1, the first
gate.

**Requirements:**

- `src/nbsnap/import_/preflight.py` with
  `check_versions(manifest, destination_status, *, max_skew) ->
  list[Finding]`.
- `Finding` dataclass: `category: Literal["precondition", "data-conflict",
  "validation"]`, `code: str`, `message: str`, `severity: str`.
- Refuse with a precondition finding if `snapshot_format_version`
  major mismatches the importer's supported range.
- Refuse if `Status.version_skew(manifest.source.netbox_version,
  destination_status.netbox_version) > max_skew`.

**Testing:** unit test in `tests/unit/test_preflight_versions.py`.
Cases: matching versions (clean), patch skew with `max_skew=patch`
(clean), minor skew with `max_skew=patch` (refused with code
`VERSION_SKEW_EXCEEDS`).

**Estimated Effort:** 1-2h

### [FEAT-18b] Pre-flight content-type coverage check

**Context:** `docs/frictions/02` M4. Surface missing content types
on the destination before any write.

**Requirements:**

- `check_content_types(manifest_refs, destination_cache) ->
  list[Finding]`.
- For each `_ref` value used in the snapshot (collected at export
  time into the manifest), confirm the destination cache has it.
- Each miss is a precondition finding with code
  `MISSING_CONTENT_TYPE` and a hint string about which plugin
  likely provides it.

**Testing:** unit test in `tests/unit/test_preflight_content_types.py`.
Cases: all refs covered (clean), one missing `netbox_bgp.bgpsession`
(refused, message mentions `netbox-bgp`).

**Estimated Effort:** 1-2h

### [FEAT-18c] Pre-flight custom-field reconciliation report

**Context:** `docs/frictions/03` M2. Surface CF mismatches before
any write.

**Requirements:**

- `check_custom_fields(snapshot_cfs, destination_cfs, *,
  in_scope_cts: set[str]) -> list[Finding]`.
- **Walk every CF defined on either side per Q22 burndown.**
  Categorise each CF as `SAME`, `MISSING_DEST` (will be created),
  `MISMATCH_TYPE`, `MISMATCH_CHOICES`. Each finding carries an
  `is_blocking` flag computed as
  `bool(cf.object_types & in_scope_cts)`. CFs decorating any
  in-scope content type produce blocking findings under the usual
  per-category policy. CFs decorating only out-of-scope content
  types produce informational findings with no exit-code impact.
- Surface a per-CF row in the report, with `is_blocking` rendered
  as a marker in the table (`[blocking]` vs `[info]`).
- Snapshot still only carries CF values on in-scope objects, the
  broader walk drives the report only.

**Testing:** unit test in `tests/unit/test_preflight_cfs.py` with
fixtures covering each category and both blocking and non-blocking
paths. Cases, `MISMATCH_TYPE` on a `dcim.device`-decorating CF is
blocking. `MISMATCH_TYPE` on a `tenancy.contact`-decorating CF is
informational. `MISSING_DEST` on an in-scope CF surfaces as a
blocking finding the operator must address before import.

**Estimated Effort:** 1-2h

### [FEAT-19a] NK index builder with brief=true

**Context:** `docs/05-export-import-workflow.md` Phase I2,
`docs/frictions/10` M8.

**Requirements:**

- `src/nbsnap/import_/index.py` with `class NKIndex`.
- `NKIndex.bulk_load(http, content_types: Iterable[str])` iterates
  each endpoint with `?brief=true`, resolves NK per row, populates
  the index.
- Internally `dict[str, dict[NK, int]]`.

**Testing:** integration test in `tests/integration/test_nk_index_brief.py`
against the seeded destination. Bulk-load `dcim.site` and
`dcim.devicerole`. Confirm `index.get("dcim.devicerole", ("access_switch",))`
returns the destination id.

**Estimated Effort:** 1-2h

### [FEAT-19b] NK index lookup and insertion

**Context:** Phase-1 writer needs both reads and writes against the
index as it goes.

**Requirements:**

- `NKIndex.get(content_type, nk) -> int | None`.
- `NKIndex.put(content_type, nk, dest_id)`.
- `NKIndex.has(content_type, nk) -> bool`.
- `NKIndex.iter(content_type) -> Iterable[tuple[NK, int]]` for the
  audit log.

**Testing:** unit test in `tests/unit/test_nk_index_ops.py`. Confirm
put/get/has round-trip. Confirm `get` on a missing key returns
`None`.

**Estimated Effort:** 1-2h

### [FEAT-20a] FK resolver for simple FKs against the index

**Context:** `docs/05-export-import-workflow.md` Phase I3.

**Requirements:**

- `src/nbsnap/import_/fk_resolve.py` with `resolve_fks(record, *,
  index, ct_cache, deferred_fields: set[str]) -> dict`.
- For each `{"_ref": ct, "_nk": nk}` value in the record, replace
  with `index.get(ct, nk)`.
- If a field is in `deferred_fields`, leave as `None` and record
  the deferred mapping for Phase 2.
- If a non-deferred FK has no index entry, raise `UnresolvedFK`
  (planner bug, hard abort).

**Testing:** unit test in `tests/unit/test_fk_resolve_simple.py`.
With a pre-loaded index, confirm a Device record's `site` and `role`
are resolved to destination ids. Confirm `primary_ip4` in
`deferred_fields` stays `None` even if the IP exists in the index.

**Estimated Effort:** 1-2h

### [FEAT-20b] FK resolver for M2M lists

**Context:** Tags, tagged VLANs, custom-field object_types.

**Requirements:**

- Extend `resolve_fks` to handle list values: resolve each element,
  return a list of destination ids.
- **Sort the result by destination id (numeric, ascending) per
  Q18 burndown.** Matches the export-side sort in `FEAT-12b`. The
  upsert equality check in `FEAT-21b` compares the snapshot-side
  natural-key tuple sort with the destination-side numeric id sort
  through a helper that walks both representations together.
- If any element is unresolvable and the M2M is required, raise
  `UnresolvedFK`. If the M2M is optional, drop the element and
  warn.

**Testing:** unit test in `tests/unit/test_fk_resolve_m2m.py`. An
Interface record with `tagged_vlans = [{"_ref": ..., "_nk": ...}, ...]`.
With both targets in the index, confirm both ids land in the output.
With one target missing and the M2M optional, confirm that element
is dropped with a warning.

**Estimated Effort:** 1-2h

### [FEAT-20c] FK resolver for polymorphic generic FKs

**Context:** IPAddress.assigned_object and friends.

**Requirements:**

- Extend `resolve_fks` to recognise polymorphic edges (via
  `openapi.field_spec`).
- Produce TWO output fields: `<field>_type` (the natural
  `app.model` string, since NetBox's write API accepts it per
  `docs/frictions/02` M3) and `<field>_id` (the destination id from
  the index).
- If the destination's API rejects the string form, fall back to the
  numeric content-type id from `ct_cache`.

**Testing:** unit test in `tests/unit/test_fk_resolve_polymorphic.py`.
Resolve an IPAddress assigned to an Interface. Confirm
`assigned_object_type == "dcim.interface"` (string form). Mock the
API to reject the string form, confirm fallback to the numeric id.

**Estimated Effort:** 1-2h

### [FEAT-21a] Upsert lookup by natural key

**Context:** `docs/05-export-import-workflow.md` "Idempotency
contract".

**Requirements:**

- `src/nbsnap/import_/upsert.py` with `find_existing(http, endpoint,
  nk_filter: dict) -> int | None`.
- `nk_filter` is the API query string derived from the NK (e.g.
  `{"site__slug": "hall-d", "name": "D39A"}`).
- Helper `nk_to_filter(content_type, nk) -> dict` per content type,
  driven by `DEFAULT_SPECS`.
- Raise `MultipleMatches` if the lookup returns >1.

**Testing:** unit test in `tests/unit/test_upsert_lookup.py` with
mocked HTTP. Confirm a single-match returns the id. Confirm a
zero-match returns `None`. Confirm a multi-match raises.

**Estimated Effort:** 1-2h

### [FEAT-21b] Skip-if-equal comparison and PATCH issuance

**Context:** the second half of the upsert path. Equality comparison
uses the same allowlist as the exporter.

**Requirements:**

- `upsert(http, content_type, body) -> UpsertResult` with
  `result: Literal["CREATED", "PATCHED", "NOOP"]`, `dest_id: int`.
- If no existing match: POST using the POST allowlist subset,
  result is `CREATED`.
- If existing match: GET, compute the diff vs `body` restricted to
  the PATCH allowlist subset, PATCH if non-empty (result
  `PATCHED`) else `NOOP`.
- **Field equality rules per Q18 burndown.** Treats `None` and
  missing key as equivalent. M2M list fields compared as sorted
  sets, the snapshot-side natural-key sort and the destination-side
  id sort are normalised through `_normalise_m2m(value, content_type,
  field)` before compare. This prevents the destination's
  Django-default ordering from triggering a spurious PATCH on every
  idempotent re-run.

**Testing:** unit test in `tests/unit/test_upsert_skip_if_equal.py`
with mocked HTTP. Cases: no match -> POST -> CREATED, match with
identical body -> no PATCH -> NOOP, match with one field different
-> PATCH with only that field -> PATCHED.

**Estimated Effort:** 1-2h

### [FEAT-22a] Phase-1 writer streaming loop

**Context:** `docs/05-export-import-workflow.md` Phase I3.

**Requirements:**

- `src/nbsnap/import_/phase1.py` with `run_phase1(http, snapshot_dir,
  *, index, ct_cache, openapi, deferred_fields_per_type) ->
  Phase1Summary`.
- For each content type in `plan.write_order`, open the matching
  `.jsonl` file, stream records, resolve FKs, upsert, insert id back
  into the index.
- Stop processing a file on any error category `precondition` or
  `planner`.

**Testing:** integration test in `tests/integration/test_phase1.py`
running Phase 1 against an empty destination with a small snapshot
fixture. Confirm Devices and their dependencies land in topo order.

**Estimated Effort:** 1-2h

### [FEAT-22b] Phase-1 audit log emitter

**Context:** every write attempt lands in a structured audit log so
the operator can grep.

**Requirements:**

- `src/nbsnap/import_/audit.py` with `class AuditLog`.
- `log_write(content_type, nk, result, dest_id, fields_changed: list[str])`.
- File location: `<snapshot>/_import.audit.jsonl`.
- One JSON line per call, plus a final summary line on close.

**Testing:** unit test in `tests/unit/test_audit_log.py`. Log three
events, close, confirm the file has three event lines plus a summary
line. Confirm `summary` aggregates `result` counts.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-23] Phase-2 deferred-FK writer

#### Architectural specification

NetBox's network model is **cyclic** in a few well-known places.
The friction note `docs/frictions/01-cyclical-foreign-keys.md`
catalogues the cases; the load-bearing one is:

    Device.primary_ip4 → IPAddress
    IPAddress.assigned_object → Interface
    Interface.device → Device

Creating any of these in isolation is impossible because each
needs another to already exist. The import engine therefore
runs in two phases:

* **Phase-1** creates every record without its cycle-closing
  FK. Devices land with `primary_ip4 = null`. IPAddresses land
  with their `assigned_object` set (the interface). Interfaces
  land with their `device` set.
* **Phase-2** walks a queue of deferred FKs and issues one
  PATCH per (record, field) tuple to fill in the cycle-closing
  references after both endpoints exist.

The queue is populated in two ways:

1. The **planner** (see FEAT-06b) statically identifies nullable
   self-loops and writes them to `_deferred.jsonl` at export
   time. These are the obvious cycle-closers.
2. The **demand-driven resolver** (FEAT-36b) pushes
   `DeferredFK` entries into an in-memory queue when its
   recursion detects a runtime cycle.

This ticket implements the writer that consumes both sources
and PATCHes the destination.

#### REST API details

Phase-2 issues one PATCH per deferred entry:

    PATCH /api/<endpoint>/<destination_id>/
    Authorization: Token <token>
    Content-Type: application/json

    {"<field_name>": <resolved_value>}

The resolved value is the destination id for FK fields, or the
literal value for non-FK deferred fields. The PATCH body is
intentionally minimal (one field) so NetBox's audit log shows
exactly what changed.

Endpoint mappings come from the same `CONTENT_TYPE_ENDPOINTS`
table the upsert path uses (see
`src/nbsnap/natkey/verify.py`).

NetBox returns:
* `200 OK` with the full updated record on success.
* `400 Bad Request` if the FK target id no longer resolves
  (very rare; happens if the destination was mutated between
  Phase-1 and Phase-2).
* `404 Not Found` if the parent record was deleted between
  Phase-1 and Phase-2.

The Swagger UI's `partial_update` operation for each content
type documents the expected request shape:
`https://demo.netbox.dev/api/schema/swagger-ui/`.

#### Implementation

```python
# src/nbsnap/import_/phase2.py
"""Phase-2 deferred FK writer.

Walks the deferred-FK queue produced by Phase-1 (planner static
defers + look-ahead runtime defers) and PATCHes each entry on
the destination. Minimal PATCH bodies, one field each, so the
destination's audit log shows exactly what changed and why.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from nbsnap.http.client import NetboxHTTP, NetboxHTTPError
from nbsnap.import_.lookahead import DeferredFK
from nbsnap.import_.nk_index import NKIndex
from nbsnap.natkey.model import NKRegistry
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS

logger = logging.getLogger(__name__)


@dataclass
class Phase2Summary:
    """Per-run audit of the Phase-2 PATCH pass."""

    counts: Counter[str] = field(default_factory=Counter)  # "patched", "skipped", "failed"
    failures: list[tuple[DeferredFK, str]] = field(default_factory=list)

    def is_clean(self) -> bool:
        return self.counts.get("failed", 0) == 0


def run_phase2(
    http: NetboxHTTP,
    deferred_queue: Iterable[DeferredFK],
    *,
    dest_index: NKIndex,
    registry: NKRegistry,
) -> Phase2Summary:
    """PATCH each deferred FK on the destination.

    Args:
        http: A writable NetboxHTTP bound to the destination.
        deferred_queue: Iterable of DeferredFK entries produced
            by Phase-1 (planner statics + look-ahead runtime).
        dest_index: The NKIndex built during Phase-1; should now
            contain every record created in Phase-1.
        registry: Natural-key registry for resolving target NKs
            against the index.
    """

    summary = Phase2Summary()

    for entry in deferred_queue:
        # Look up the child record's destination id (it was
        # created in Phase-1 without this FK).
        child_id = dest_index.lookup(entry.child_content_type, entry.child_nk)
        if child_id is None:
            logger.warning(
                "Phase-2: child %s NK=%r not on destination, skipping",
                entry.child_content_type, entry.child_nk,
            )
            summary.counts["skipped"] += 1
            continue

        # Look up the target's destination id. Build the target
        # NK index first; for self-loops (devicerole.parent ->
        # devicerole) the same content type already happens to
        # be built, so this is usually a no-op.
        dest_index.ensure_built(http, registry, entry.target_content_type)
        target_id = dest_index.lookup(entry.target_content_type, entry.target_nk)
        if target_id is None:
            logger.warning(
                "Phase-2: target %s NK=%r still missing, skipping %s.%s",
                entry.target_content_type, entry.target_nk,
                entry.child_content_type, entry.field_name,
            )
            summary.counts["skipped"] += 1
            continue

        # Issue the minimal PATCH. One field, the deferred FK.
        endpoint = CONTENT_TYPE_ENDPOINTS[entry.child_content_type]
        try:
            http.patch(f"{endpoint}{child_id}/", {entry.field_name: target_id})
        except NetboxHTTPError as exc:
            logger.warning(
                "Phase-2 PATCH failed for %s id=%d field=%s: HTTP %d %s",
                entry.child_content_type, child_id, entry.field_name,
                exc.status, exc.body[:200],
            )
            summary.counts["failed"] += 1
            summary.failures.append((entry, str(exc)))
            continue

        summary.counts["patched"] += 1
        logger.info(
            "Phase-2 PATCH %s id=%d %s -> %s id=%d",
            entry.child_content_type, child_id, entry.field_name,
            entry.target_content_type, target_id,
        )

    return summary
```

Wire into the driver right after the Phase-1 loop ends:

```python
# src/nbsnap/import_/driver.py
from nbsnap.import_.phase2 import run_phase2

def run_import(http, snapshot_dir, ...):
    ...
    # Phase-1 loop populates deferred_queue and dest_index.
    ...
    phase2 = run_phase2(http, deferred_queue, dest_index=index, registry=registry)
    summary.phase2 = phase2  # surface in the CLI's audit output
    return summary
```

#### Integration test

```python
# tests/integration/test_phase2_deferred.py
"""End-to-end Phase-2 against the netbox-docker test stacks.

Seeds the source with a device whose primary_ip4 points at its
own Vlan600 interface IP (the classic Device <-> IPAddress
cycle), runs export, runs import, asserts the destination
device has primary_ip4 set after the Phase-2 pass."""

import pytest

from nbsnap.export.driver import run_export
from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.driver import run_import
from nbsnap.schema.status import VersionSkew


@pytest.mark.usefixtures("require_stack")
def test_phase2_patches_primary_ip4(tmp_path) -> None:
    src = NetboxHTTP("http://localhost:8080", "0123456789abcdef" * 2 + "01234567")
    dst = NetboxHTTP("http://localhost:8081", "abcdef0123456789" * 2 + "abcdef01")
    snap_dir = tmp_path / "snap"

    run_export(src, snap_dir)
    summary = run_import(dst, snap_dir, max_skew=VersionSkew.MINOR, on_error="continue")

    # Phase-2 should have PATCHed primary_ip4 on every device that
    # had it set in the source. The seed has 2 devices with primary_ip4.
    assert summary.phase2.counts["patched"] >= 2
    assert summary.phase2.is_clean(), summary.phase2.failures

    # Cross-check via the destination's own GET.
    import requests
    resp = requests.get(
        "http://localhost:8081/api/dcim/devices/?name=d39a",
        headers={"Authorization": f"Token {dst._token}"},
        timeout=10,
    )
    device = resp.json()["results"][0]
    assert device["primary_ip4"] is not None
    assert device["primary_ip4"]["address"] == "172.16.1.10/24"
```

#### Acceptance criteria

* After Phase-1 + Phase-2 against the renderer-minimum fixture,
  every device that had `primary_ip4` set on the source has it
  set on the destination too.
* `Phase2Summary.is_clean()` returns True on a healthy run.
* Re-running the same import is a no-op: `patched: 0`,
  `skipped: 0`, `failed: 0` because every FK is already in
  place.
* The destination's audit log shows one PATCH per deferred FK,
  with a minimal one-field body.

**Estimated Effort:** 1-2h

### [FEAT-24a] Result and Category dispatcher core

**Context:** `docs/05-export-import-workflow.md` "Error categorisation".

**Requirements:**

- `src/nbsnap/import_/errors.py` with `enum Category`:
  `PRECONDITION`, `DATA_CONFLICT`, `VALIDATION`, `TRANSIENT`,
  `PLANNER`.
- `@dataclass Result(category: Category, payload: Any)`.
- `class PolicyDispatcher` with per-category policy mapping
  (`ABORT`, `SKIP_AND_LOG`, `PROMPT`).
- `dispatch(result) -> Action` returns the next move (continue,
  abort, prompt the operator).

**Testing:** unit test in `tests/unit/test_error_dispatcher.py`.
Build a dispatcher with default policy, confirm a PRECONDITION
result yields ABORT, a VALIDATION result yields SKIP_AND_LOG.
Override the validation policy to ABORT, confirm dispatch reflects
the override.

**Estimated Effort:** 1-2h

### [FEAT-24b] --on-error CLI flag parsing

**Context:** `--on-error category=policy` repeatable flag.

**Requirements:**

- `src/nbsnap/import_/cli.py` accepts `--on-error` as `append`
  action.
- Parse each value as `<category>=<policy>` and feed the dispatcher.
- Reject unknown categories or policies with a clear error.

**Testing:** unit test in `tests/unit/test_on_error_parsing.py`.
Cases: valid `validation=abort`, invalid `garbage=abort` (reject),
invalid `validation=eat` (reject). Confirm multiple `--on-error`
flags accumulate.

**Estimated Effort:** 1-2h

### [FEAT-25a] nbsnap import CLI argument parser

**Context:** mirrors `FEAT-17a` for the import side.

**Requirements:**

- `src/nbsnap/import_/cli.py` with `add_import_parser(subparsers)`.
- Flags (per Q21 burndown, two earlier flags dropped):
  `--url`, `--token`, `--in`, `--dry-run`,
  `--max-version-skew`, `--reject-existing`,
  `--allow-source-install-ips`, `--on-error`,
  `--allow-missing-models`, `--no-verify-tls`. Drop
  `--include-password-hashes` and `--source-db-url`.
- Env precedence: flag, `NB_DESTINATION_URL`/`NB_DESTINATION_TOKEN`,
  legacy `NB_URL`/`NB_TOKEN`.

**Testing:** unit test in `tests/unit/test_import_cli_args.py`. Parse
a representative argv, assert namespace. Confirm
`NB_DESTINATION_TOKEN` is consumed when `--token` is absent. Confirm
that the dropped flags (`--include-password-hashes`,
`--source-db-url`) raise an `argparse.ArgumentError` so an operator
using a stale runbook gets a clear pointer to the network-only
scope change.

**Estimated Effort:** 1-2h

### [FEAT-25b] Wire import CLI to Phase 1, Phase 2, and verify

**Context:** the parser from `FEAT-25a` runs the engine.

**Requirements:**

- `run_import(args) -> int`.
- Read manifest, run preflight, build NK index, run Phase 1, run
  Phase 2.
- Optionally run `verify.roundtrip_check` if not `--dry-run`.
- Print end-of-run summary table (`FEAT-30`).
- Exit codes: 0 ok, 1 user error, 2 import refused by preflight, 3
  partial failure.

**Testing:** integration test in `tests/integration/test_import_smoke.py`.
Seed source, export, import into empty destination, confirm exit 0
and Device count matches the seed.

**Estimated Effort:** 1-2h

### [TEST-06] Idempotency two-run integration test

**Context:** `goals.md` success criterion 2.

**Requirements:**

- `tests/integration/test_import_idempotency.py`.
- Seed source, export, import to fresh destination, inspect audit
  log assert every result is CREATED.
- Re-run import without changes, inspect audit log assert every
  result is NOOP and no PATCH was sent.

**Testing:** run the test, confirm green. Inject a deliberate
extra field on one record in the audit comparison code, confirm the
test fails with a clear pointer to the offending content type.

**Estimated Effort:** 1-2h

### [TEST-07] Cycle resolution end-to-end integration test

**Context:** `docs/frictions/01`. The headline test for the cycle
machinery.

**Requirements:**

- `tests/integration/test_import_cycles.py`.
- Seed source with a Device + Interface + IPAddress chain where
  Device.primary_ip4 points at the IP.
- Export, import.
- GET the device on the destination, assert `primary_ip4.address`
  matches the source's value.
- Repeat for IPv6 if seeded.

**Testing:** run the test, confirm green. Comment out the Phase 2
writer call in `run_import`, re-run, confirm the test fails with a
clear assertion message.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36-blocker] Enum-dict serialisation mismatch on write

**Context:** the actual production rescue import (logged at
`tmp/nbsnap-rescue-10/import-attempt-3.log`) failed 5105 of 5153
rows. The root cause is **not** ordering, it is the enum-dict
shape: NetBox's GET response surfaces enum fields as
`{"value": "active", "label": "Active"}`, the matching write
endpoint refuses that shape and expects the bare value string.

#### Architectural specification

NetBox's REST API is asymmetric on choice fields. The schema
generator emits a JSON-schema `string` with an `enum:` list for
write endpoints, but the response serializer wraps the same
value in a `{value, label}` envelope for human readability. The
asymmetry is documented in the NetBox REST API overview:
`https://netboxlabs.com/docs/netbox/integrations/rest-api/`,
section "Brief Format" and the choice-field examples in the
Swagger UI at `https://demo.netbox.dev/api/schema/swagger-ui/`.

Sites are the obvious example. The GET response is

    GET /api/dcim/sites/1/
    {
      "id": 1, "name": "Hall-A", "slug": "a",
      "status": {"value": "active", "label": "Active"},
      ...
    }

but the write body must be

    POST /api/dcim/sites/
    {
      "name": "Hall-A", "slug": "a",
      "status": "active",
      ...
    }

The snapshot wrote the GET shape verbatim, so every record with
a `status`, `airflow`, `face`, `type`-on-interface, or any other
enum field is rejected by NetBox before its FKs are even
evaluated. Sites failed to POST, dependent locations / racks /
devices cannot resolve them, and the import cascade fans out
from there.

The fix lives on the export side (the snapshot is the long-
lived artefact, so the canonical fix updates the stored shape).
An import-side coerce stays as a belt-and-braces fallback so
old snapshots remain importable without re-exporting.

#### REST API details

* Affected endpoints, every write endpoint that has at least
  one choice field in its request body. Among the renderer-
  minimum scope, these are confirmed via the Swagger UI:
  * `POST/PATCH /api/dcim/sites/` (`status`)
  * `POST/PATCH /api/dcim/locations/` (`status`)
  * `POST/PATCH /api/dcim/racks/` (`status`, `type`,
    `outer_unit`, `width`)
  * `POST/PATCH /api/dcim/devices/` (`status`, `airflow`,
    `face`)
  * `POST/PATCH /api/dcim/interfaces/` (`type`, `mode`,
    `duplex`, `rf_role`, `rf_channel_width`, `poe_mode`,
    `poe_type`)
  * `POST/PATCH /api/dcim/cables/` (`status`, `type`,
    `length_unit`)
  * `POST/PATCH /api/ipam/ip-addresses/` (`status`, `role`)
  * `POST/PATCH /api/ipam/prefixes/` (`status`)
  * `POST/PATCH /api/ipam/ip-ranges/` (`status`)
  * `POST/PATCH /api/ipam/vlans/` (`status`)
* Detection rule: a field value is an enum-dict iff it is a
  mapping with **exactly two keys** `{"value", "label"}` and
  both values are strings.
* OpenAPI cross-check, optional but cheap: the field's request
  schema has `type: string` (possibly with `enum: [...]`) or is
  an `allOf` wrapper whose target is a string-enum component.

Example minimal reproducer against any NetBox install:

    # Reading: enum-dict shape
    curl -sH "Authorization: Token $T" \
        https://netbox.example.com/api/dcim/sites/1/ \
        | jq .status
    # -> {"value": "active", "label": "Active"}

    # Writing the same shape, NetBox 400s:
    curl -sX PATCH -H "Authorization: Token $T" \
        -H "Content-Type: application/json" \
        -d '{"status": {"value": "active", "label": "Active"}}' \
        https://netbox.example.com/api/dcim/sites/1/
    # -> HTTP 400 {"status":["Value must be passed directly..."]}

    # Writing the flat value succeeds:
    curl -sX PATCH -H "Authorization: Token $T" \
        -H "Content-Type: application/json" \
        -d '{"status": "active"}' \
        https://netbox.example.com/api/dcim/sites/1/
    # -> HTTP 200

#### Implementation, export side (canonical)

Add `_collapse_enum_dict` to `src/nbsnap/export/extractor.py`
and call it from `_apply_allowlist`:

```python
# src/nbsnap/export/extractor.py

from collections.abc import Mapping
from typing import Any

# NetBox's choice-field response shape: a dict with exactly
# these two keys. We collapse it to the bare value string so
# the snapshot matches what NetBox's write endpoints accept.
_ENUM_DICT_KEYS = frozenset({"value", "label"})


def _collapse_enum_dict(value: Any) -> Any:
    """If `value` is a NetBox enum-dict, return its `value` slot.

    NetBox 4.x serialises enum fields as
    `{"value": "active", "label": "Active"}` on GET but expects
    only `"active"` on POST/PATCH. Recognise the shape by the
    exact key set so we never accidentally collapse a real
    payload dict that happens to carry a `value` field.
    """
    if isinstance(value, Mapping) and frozenset(value.keys()) == _ENUM_DICT_KEYS:
        inner = value["value"]
        # NetBox stores the writable enum as a string; defend
        # against unusual shapes (numbers, nested dicts) by
        # passing them through untouched.
        if isinstance(inner, (str, int, bool)) or inner is None:
            return inner
    return value


def _apply_allowlist(record: Mapping[str, Any], allowlist: frozenset[str]) -> dict[str, Any]:
    """Keep only allowlisted fields AND collapse enum-dicts."""

    return {k: _collapse_enum_dict(v) for k, v in record.items() if k in allowlist}
```

That single change is enough to flip the 5105-failure import
into success for every row that was previously stuck on the
enum 400.

#### Implementation, import side (defensive fallback)

So old snapshots exported before the fix still upload cleanly,
add the same coercion right before the POST/PATCH body is
serialised. In `src/nbsnap/import_/upsert.py`:

```python
# src/nbsnap/import_/upsert.py

from nbsnap.export.extractor import _collapse_enum_dict

def _coerce_body_for_write(body: dict) -> dict:
    """Defensive: collapse any enum-dicts still present in the
    body. Should be a no-op for snapshots produced after
    FEAT-36-blocker landed."""
    return {k: _collapse_enum_dict(v) for k, v in body.items()}


# Inside upsert, just before the POST and inside the PATCH diff:
created = http.post(endpoint, _coerce_body_for_write(dict(body)))
# ...
http.patch(f"{endpoint}{existing_id}/", _coerce_body_for_write(diff))
```

#### Regression test

```python
# tests/unit/test_export_enum_collapse.py
from nbsnap.export.extractor import _collapse_enum_dict, _apply_allowlist


def test_collapses_enum_dict() -> None:
    """The classic NetBox GET-response enum-dict collapses to the value."""

    assert _collapse_enum_dict({"value": "active", "label": "Active"}) == "active"


def test_passes_through_non_enum_dicts() -> None:
    """A regular dict (e.g. a nested object) must not be collapsed."""

    nested = {"id": 7, "name": "Hall-D"}
    assert _collapse_enum_dict(nested) == nested

    # Even a dict that *contains* a "value" key but more keys
    # besides {value, label} stays intact.
    rich = {"value": "x", "label": "X", "id": 1}
    assert _collapse_enum_dict(rich) == rich


def test_passes_through_non_dicts() -> None:
    assert _collapse_enum_dict("active") == "active"
    assert _collapse_enum_dict(7) == 7
    assert _collapse_enum_dict(None) is None
    assert _collapse_enum_dict([]) == []


def test_apply_allowlist_collapses_status_inline() -> None:
    """End-to-end, _apply_allowlist on a sample site row turns
    status from {value,label} into the bare string."""

    raw = {
        "id": 1,
        "name": "Hall-A",
        "slug": "a",
        "status": {"value": "active", "label": "Active"},
        "tenant": None,
    }
    allowlist = frozenset({"name", "slug", "status"})
    out = _apply_allowlist(raw, allowlist)
    assert out == {"name": "Hall-A", "slug": "a", "status": "active"}
```

#### Acceptance criteria

After this ticket lands and a re-export is run against the
production source:

1. `head -1 snapshot/dcim/sites.jsonl | jq .body.status` returns
   `"active"` (the string), not the dict.
2. A fresh import against an empty destination NetBox 4.6.x
   produces `created: <high>`, `failed: ~0` on the renderer-
   minimum scope (residual failures should only be the polymorphic
   ordering issues FEAT-36d / FEAT-36b address).
3. Running the import against a pre-fix snapshot (no re-export)
   still works because the import-side coerce in `upsert.py`
   handles the legacy shape.

**Estimated Effort:** 1-2h

**Priority:** BLOCKER, every other FEAT-36 ticket below is
nearly useless until this lands because the records they would
upsert get rejected at the HTTP layer.

### [REFINED] [FEAT-36a] Snapshot natural-key index loader

#### Architectural specification

The look-ahead resolver (`FEAT-36b`) needs to answer two
questions instantly: "does the snapshot itself carry the record
identified by `(content_type, NK)`?" and "if yes, what is the
body it carried?". A pure-in-memory map keyed by the natural-key
tuple gives both for O(1).

The SnapshotIndex is read-only after construction and is built
**once** at the very top of `run_import`, before any other
work. Subsequent FK resolutions consult it without any disk or
network IO. Memory cost is bounded by the snapshot's total row
count (the renderer-minimum scope tops out around 50k rows /
~5 MB JSON; production-sized snapshots have been observed in
the 200k row / 20 MB range, well below the budget a NetBox
operator already pays for a `pynetbox` GET-all of any single
content type).

This ticket does NOT touch NetBox's REST API; the data source
is the snapshot directory on disk. The NetBox-side counterpart
is `NKIndex` (already in `src/nbsnap/import_/nk_index.py`),
which builds the destination side from `GET ?brief=true`
listings. The two indices together form a two-tier lookup: the
NKIndex says "does the destination have this NK now?", the
SnapshotIndex says "if not, can we create it from the snapshot?".

#### REST API details

None directly. The SnapshotIndex is a pure local helper.

For context the consumer (FEAT-36b) will pair it with NetBox
calls of this shape:

    GET  /api/<endpoint>/?brief=true         (NKIndex build)
    POST /api/<endpoint>/                    (creating a missing target)

documented in the NetBox Swagger UI at
`https://demo.netbox.dev/api/schema/swagger-ui/` under each
content type's `list` and `create` operations.

#### Implementation

```python
# src/nbsnap/import_/snapshot_index.py
"""In-memory (content_type, NK) -> body map for look-ahead.

Built once at the start of run_import, queried thereafter
without any IO. The body dict is stored as-is from the
snapshot's JSONL, so the consumer should not mutate it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A natural key in the snapshot is a JSON-deserialised list-of-
# lists. We normalise to tuple-of-tuples at load time so the
# index key is hashable and comparisons work cleanly.
NaturalKey = tuple


def _to_tuple(value: Any) -> Any:
    """Recursively convert lists to tuples so the value is hashable."""
    if isinstance(value, list):
        return tuple(_to_tuple(v) for v in value)
    return value


@dataclass
class SnapshotIndex:
    """Maps `(content_type, NK)` -> snapshot body.

    Memory footprint per row is approximately the size of the
    JSON body that the export emitted. At ~100 bytes per row for
    the renderer-minimum scope, a 50,000-row snapshot consumes
    ~5 MB of RAM. Plus a small constant overhead per dict entry.
    Operators with very large NetBoxes (>500k rows) may want to
    add a streaming variant in a follow-up, today's footprint is
    well within typical operator-host RAM.
    """

    _by_key: dict[tuple[str, NaturalKey], dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_snapshot(cls, snapshot_dir: Path) -> SnapshotIndex:
        """Walk every JSONL under `snapshot_dir`, build the index."""

        index = cls()
        # Map of relative jsonl path -> content type. We derive the
        # content type from the CONTENT_TYPE_FILES table that the
        # writer already uses, so the two stay in sync.
        from nbsnap.export.writer import CONTENT_TYPE_FILES

        ct_by_rel = {rel: ct for ct, rel in CONTENT_TYPE_FILES.items()}

        for jsonl_path in snapshot_dir.rglob("*.jsonl"):
            # Skip top-level audit/progress logs, they are not records.
            if jsonl_path.name in {"flags.jsonl", "progress.jsonl", "_deferred.jsonl"}:
                continue
            rel = jsonl_path.relative_to(snapshot_dir).as_posix()
            content_type = ct_by_rel.get(rel)
            if content_type is None:
                # Unknown jsonl, don't index it; the importer will
                # log this as an out-of-scope row when it gets there.
                continue
            for row in _iter_jsonl(jsonl_path):
                nk = _to_tuple(row.get("natural_key"))
                body = row.get("body") or {}
                if isinstance(body, dict):
                    index._by_key[(content_type, nk)] = body
        return index

    def lookup(
        self, content_type: str, natural_key: NaturalKey
    ) -> dict[str, Any] | None:
        """Return the snapshot body for `(content_type, NK)` or None."""
        return self._by_key.get((content_type, _to_tuple(natural_key)))

    def has(self, content_type: str, natural_key: NaturalKey) -> bool:
        """Constant-time membership check."""
        return (content_type, _to_tuple(natural_key)) in self._by_key

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[tuple[str, NaturalKey]]:
        return iter(self._by_key.keys())


def _iter_jsonl(path: Path) -> Iterator[Mapping[str, Any]]:
    """Stream a JSONL file, skip blank / malformed lines silently."""
    with path.open(encoding="utf-8") as fp:
        for raw in fp:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                # Malformed row; the export pipeline already drops
                # these via flags.jsonl, but be defensive.
                continue
```

Wire into the driver:

```python
# src/nbsnap/import_/driver.py

from nbsnap.import_.snapshot_index import SnapshotIndex

def run_import(http, snapshot_dir, *, max_skew=VersionSkew.MINOR, on_error="stop"):
    ...
    # Build the snapshot-side NK lookup table once, up front.
    snapshot_index = SnapshotIndex.from_snapshot(snapshot_dir)
    # Pass snapshot_index into _resolve_body / upsert so FEAT-36b's
    # demand-driven path can consult it on a destination miss.
    ...
```

#### Regression test

```python
# tests/unit/test_import_snapshot_index.py
import json
from pathlib import Path

from nbsnap.import_.snapshot_index import SnapshotIndex


def _write_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(row) + "\n")


def test_index_finds_records_by_nk(tmp_path: Path) -> None:
    """A small snapshot dir is indexed; lookup returns the body."""

    _write_row(
        tmp_path / "dcim" / "sites.jsonl",
        {"natural_key": ["hall-a"], "body": {"name": "Hall-A", "slug": "a"}},
    )
    _write_row(
        tmp_path / "dcim" / "devices.jsonl",
        {
            "natural_key": [["hall-a"], "d39a"],
            "body": {"name": "d39a"},
        },
    )

    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert idx.has("dcim.site", ("hall-a",))
    assert idx.lookup("dcim.site", ("hall-a",)) == {"name": "Hall-A", "slug": "a"}
    # Composite NK with a nested tuple.
    assert idx.lookup("dcim.device", (("hall-a",), "d39a")) == {"name": "d39a"}


def test_missing_nk_returns_none(tmp_path: Path) -> None:
    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert idx.lookup("dcim.site", ("ghost",)) is None
    assert idx.has("dcim.site", ("ghost",)) is False


def test_audit_files_are_skipped(tmp_path: Path) -> None:
    """flags.jsonl / progress.jsonl are not records, must not load."""

    (tmp_path / "flags.jsonl").write_text(
        json.dumps({"content_type": "x", "field": "y"}) + "\n",
        encoding="utf-8",
    )
    idx = SnapshotIndex.from_snapshot(tmp_path)
    assert len(idx) == 0
```

#### Acceptance criteria

* `SnapshotIndex.from_snapshot(tmp_path)` on the production-
  rescue snapshot (~5k rows) returns an index with the same row
  count as the manifest's `counts` total (within a few rows for
  the deferred / flagged cases).
* Lookup latency is in the sub-microsecond range (dict access).
* Memory footprint < 50 MB even on a 500k-row snapshot.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36b] Demand-driven FK resolution with cycle detection

#### Architectural specification

Today's FK resolver is single-tier: if the destination's
`NKIndex` does not have the target, the field is dropped and a
warning is logged. The result is a snapshot where most rows
land but most of their FKs do not, producing a
structurally-broken NetBox that "looks like the source" only
superficially.

The new resolver becomes two-tier:

1. **Destination tier** (existing `NKIndex`). Look up the target
   by `(content_type, NK)` on the destination. Hit = use the id.
2. **Snapshot tier** (`SnapshotIndex` from `FEAT-36a`). On a
   destination miss, ask "is this target in the snapshot?". If
   yes, recursively run the same upsert path Phase-1 uses on
   that target first. The recursion populates the destination
   `NKIndex` with the new id, then the original FK resolves
   correctly.

This turns plan-order from a hard requirement into a
performance optimisation. The plan still controls "what to
process now"; the resolver handles "what to pull in early when
this row needs it".

Two real-world traps:

* **Cycles.** `Device.primary_ip4 → IPAddress.assigned_object
  → Interface.device → Device`. Maintain a `processing_stack:
  set[(content_type, NK)]` threaded through the recursion. If
  the recursion sees a target already on the stack, do not
  recurse; instead push a `DeferredFK` onto a queue the driver
  walks during Phase-2 (`FEAT-36c`). The outer upsert proceeds
  without the cycle-closing field.
* **Out-of-scope targets** (e.g. `dcim.site.region →
  dcim.region`). `SnapshotIndex.lookup` returns `None`; fall
  through to the existing `_warn_dropped` path. No change to
  user-visible behaviour, but the audit log will categorise
  this differently in `FEAT-36e`.

#### REST API details

The recursive resolver calls into the same NetBox REST API
calls Phase-1 already uses. No new endpoints, just a deeper
call chain:

* **Lookup target by NK (NKIndex build, the cheap path).**

      GET /api/<endpoint>/?brief=true&limit=500

  Documented under each content type's `list` operation in the
  Swagger UI. NetBox's `brief=true` mode strips most nested
  fields, leaving only `id`, `display`, the slug or other NK
  field, and a `url`. We page via the `next` link until
  exhausted, then memoise into `NKIndex._by_key`.

* **Create the missing target (POST).**

      POST /api/<endpoint>/
      Content-Type: application/json
      Authorization: Token <token>

      <body, with all FK fields resolved to destination ids>

  Documented under each content type's `create` operation.
  Returns 201 with the created object including `id`. We then
  call `NKIndex.insert(content_type, nk, id)` so the same NK
  will hit the destination index on the next lookup.

* **Update an already-existing target (PATCH, when the snapshot
  body differs).** Re-uses the existing `upsert()` skip-if-equal
  path, see `src/nbsnap/import_/upsert.py`. The PATCH body
  carries only the differing fields:

      PATCH /api/<endpoint>/<id>/
      Content-Type: application/json
      {"<changed_field>": "<new_value>"}

#### Implementation

```python
# src/nbsnap/import_/lookahead.py
"""Demand-driven FK resolver.

When the destination NKIndex misses on an FK target, consult
the SnapshotIndex. If the target is present in the snapshot,
recursively upsert it first, then return the new destination
id so the outer FK resolves cleanly.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.import_.upsert import UpsertOutcome, upsert
from nbsnap.natkey.model import NKRegistry

logger = logging.getLogger(__name__)

# Hard cap on recursion depth so a malformed snapshot cannot
# cause unbounded growth of the processing stack. 200 is
# generous for any real NetBox topology; cycles are detected
# explicitly via the stack membership check below.
MAX_DEPTH = 200


@dataclass(frozen=True)
class DeferredFK:
    """One FK reference that could not be resolved during Phase-1
    because the target sits in a cycle with the current record.
    The Phase-2 writer (FEAT-36c) walks this queue and PATCHes
    the parent record after both endpoints exist.
    """

    child_content_type: str
    child_nk: tuple
    field_name: str
    target_content_type: str
    target_nk: tuple


def resolve_or_create(
    http: NetboxHTTP,
    snapshot_index: SnapshotIndex,
    dest_index: NKIndex,
    registry: NKRegistry,
    *,
    content_type: str,
    natural_key: tuple,
    processing_stack: set[tuple[str, tuple]],
    deferred_queue: list[DeferredFK],
    depth: int = 0,
) -> int | None:
    """Return the destination id for `(content_type, NK)`.

    Three-step strategy:
      1. Destination index hit -> return the id.
      2. Cycle detected (target already on processing stack)
         -> defer to Phase-2, return None.
      3. Snapshot has the target -> recursively upsert it,
         insert into NKIndex, return the new id.
      4. Snapshot misses too -> return None; the caller drops
         the FK via the existing _warn_dropped path.
    """
    key = (content_type, natural_key)

    # Step 1, destination already has it.
    dest_index.ensure_built(http, registry, content_type)
    existing = dest_index.lookup(content_type, natural_key)
    if existing is not None:
        return existing

    # Step 2, cycle, defer.
    if key in processing_stack:
        # Caller knows the child it is creating; the actual
        # DeferredFK gets pushed at the caller site where the
        # child_nk and field_name are available.
        return None

    if depth >= MAX_DEPTH:
        logger.warning(
            "max look-ahead depth %d hit at %s NK=%r; dropping FK",
            MAX_DEPTH, content_type, natural_key,
        )
        return None

    # Step 3, snapshot has the target.
    snapshot_body = snapshot_index.lookup(content_type, natural_key)
    if snapshot_body is None:
        return None  # Step 4, genuine miss.

    # Recurse: resolve the target's own FKs (which may
    # themselves trigger more lookups), then upsert. Adding to
    # the processing stack BEFORE the recursive _resolve_body
    # call closes the cycle detection: if any FK in the body
    # points back to ourselves, the inner resolve_or_create
    # hits step 2 and defers.
    processing_stack.add(key)
    try:
        from nbsnap.import_.driver import _resolve_body  # local import, avoid cycle
        # _resolve_body needs access to the openapi schema and
        # to the same resolution machinery; in practice we pass
        # them in via a context object the driver builds once.
        # For brevity here, we re-fetch from the driver module.
        from nbsnap.schema.openapi import OpenAPI  # noqa
        # openapi must be passed in by the caller; this code is
        # illustrative — the real implementation receives it as
        # a parameter.
        openapi = _ctx_openapi()  # provided by the driver
        resolved_body = _resolve_body(
            content_type, dict(snapshot_body), openapi, dest_index, http, registry,
            snapshot_index=snapshot_index,
            processing_stack=processing_stack,
            deferred_queue=deferred_queue,
            depth=depth + 1,
        )
        result = upsert(
            http,
            content_type=content_type,
            natural_key=natural_key,
            body=resolved_body,
            index=dest_index,
            registry=registry,
        )
        if result.outcome is UpsertOutcome.FAILED:
            logger.warning(
                "look-ahead upsert failed for %s NK=%r: %s",
                content_type, natural_key, result.message,
            )
            return None
        return result.destination_id
    finally:
        processing_stack.discard(key)


def _ctx_openapi():  # pragma: no cover, real impl in driver
    raise NotImplementedError("provided by run_import context")
```

The driver-level integration is small. Inside
`_resolve_body`'s simple-FK branch, replace the existing
`resolve_simple_fk` call with:

```python
# src/nbsnap/import_/driver.py
try:
    rid = resolve_simple_fk(
        value, spec.fk_target, dest_index,
        http=http, registry=registry,
    )
except KeyError:
    # Destination miss. Try the snapshot.
    rid = resolve_or_create(
        http, snapshot_index, dest_index, registry,
        content_type=spec.fk_target,
        natural_key=value if isinstance(value, tuple) else tuple(value),
        processing_stack=processing_stack,
        deferred_queue=deferred_queue,
    )
    if rid is None:
        # Either an out-of-scope target or a deferred cycle.
        # Drop the field; if it was a cycle, push the DeferredFK
        # so Phase-2 can PATCH it later.
        if cycle_detected:
            deferred_queue.append(
                DeferredFK(
                    child_content_type=content_type,
                    child_nk=current_nk,
                    field_name=field_name,
                    target_content_type=spec.fk_target,
                    target_nk=tuple(value),
                )
            )
        _warn_dropped(content_type, field_name, spec.fk_target, KeyError())
        continue
resolved[field_name] = rid
```

#### Regression tests

```python
# tests/unit/test_import_demand_driven.py
"""Three end-to-end scenarios for the look-ahead resolver."""

from unittest.mock import MagicMock

from nbsnap.import_.lookahead import DeferredFK, resolve_or_create
from nbsnap.import_.nk_index import NKIndex
from nbsnap.import_.snapshot_index import SnapshotIndex
from nbsnap.natkey.registry import default as default_registry


def test_forward_reference_creates_target_on_demand() -> None:
    """Device written before its Site: the resolver creates the
    Site on demand and returns its new id."""

    http = MagicMock()
    http.get_all.return_value = iter([])  # empty destination
    http.post.return_value = {"id": 42, "name": "Hall-D", "slug": "hall-d"}

    snap = SnapshotIndex()
    snap._by_key[("dcim.site", ("hall-d",))] = {"name": "Hall-D", "slug": "hall-d"}

    dest = NKIndex()
    stack: set = set()
    q: list[DeferredFK] = []
    rid = resolve_or_create(
        http, snap, dest, default_registry(),
        content_type="dcim.site",
        natural_key=("hall-d",),
        processing_stack=stack,
        deferred_queue=q,
    )
    assert rid == 42
    # Subsequent lookups for the same NK now hit dest_index.
    assert dest.lookup("dcim.site", ("hall-d",)) == 42


def test_cycle_defers_closing_fk() -> None:
    """Device → IPAddress → Interface → Device cycles. The
    inner-most attempt to recurse back into 'dcim.device d39a'
    must defer, not loop."""

    http = MagicMock()
    http.get_all.return_value = iter([])
    # Pre-populate the stack as if we are mid-recursion on Device.
    snap = SnapshotIndex()
    dest = NKIndex()
    stack = {("dcim.device", (("hall-d",), "d39a"))}
    q: list[DeferredFK] = []

    rid = resolve_or_create(
        http, snap, dest, default_registry(),
        content_type="dcim.device",
        natural_key=(("hall-d",), "d39a"),
        processing_stack=stack,
        deferred_queue=q,
    )
    assert rid is None  # cycle, deferred
    # http.post was never called; we did not try to create.
    http.post.assert_not_called()


def test_out_of_scope_target_falls_through() -> None:
    """If the snapshot does not have the target either, we just
    return None and let the caller drop the FK with a warning."""

    http = MagicMock()
    http.get_all.return_value = iter([])
    snap = SnapshotIndex()  # empty
    dest = NKIndex()
    rid = resolve_or_create(
        http, snap, dest, default_registry(),
        content_type="dcim.region",
        natural_key=("elmia",),
        processing_stack=set(),
        deferred_queue=[],
    )
    assert rid is None
    http.post.assert_not_called()
```

#### Acceptance criteria

After this ticket lands, the rescue snapshot import:

1. Reduces `failed` count from thousands to single digits.
2. Produces a non-empty `deferred_queue` only for genuine
   cycles (Device.primary_ip4 chain, devicerole.parent
   self-loops). The queue size should be roughly the number of
   devices with primary_ip4 set, plus a handful for the
   self-loop hierarchies.
3. Out-of-scope targets (`dcim.region`, `ipam.vrf`) still log
   the same single warning per `(content_type, field, target)`
   triple as today. No new noise.

**Estimated Effort:** 2-4h

### [REFINED] [FEAT-36c] Wire Phase-2 deferred-FK writer into the import driver

#### Architectural specification

`FEAT-23` (above) holds the implementation of the Phase-2
writer itself, complete with code and tests. This ticket is the
**wire-up** that makes Phase-2 actually run as part of
`nbsnap import` and surfaces its counters in the CLI summary.
It also closes the loop with `FEAT-36b` so the deferred queue
the look-ahead resolver populates flows into Phase-2 without
manual plumbing.

Two integration points:

1. **Driver wiring.** After Phase-1 finishes, the driver calls
   `run_phase2(http, deferred_queue, dest_index=..., registry=...)`
   and stores the returned `Phase2Summary` on the
   `ImportSummary` object. The Phase-1 and Phase-2 counters
   stay separate so an operator can see the split.
2. **CLI summary.** `src/nbsnap/import_cli.py` prints the
   Phase-2 counts under a separate "phase2:" block. The exit
   code accounts for Phase-2 failures the same way it accounts
   for Phase-1 failures (exit 2 on any failure).

#### REST API details

None new beyond what `FEAT-23` documents. This ticket is pure
driver glue.

#### Implementation

```python
# src/nbsnap/import_/driver.py

from nbsnap.import_.lookahead import DeferredFK
from nbsnap.import_.phase2 import Phase2Summary, run_phase2
from nbsnap.import_.snapshot_index import SnapshotIndex


@dataclass
class ImportSummary:
    preflight: PreflightReport
    counts: Counter[UpsertOutcome] = field(default_factory=Counter)
    failures: list[UpsertResult] = field(default_factory=list)
    # NEW: Phase-2 results split out so an operator sees what
    # happened in each phase separately.
    phase2: Phase2Summary | None = None


def run_import(http, snapshot_dir, *, max_skew, on_error):
    ...
    snapshot_index = SnapshotIndex.from_snapshot(Path(snapshot_dir))
    deferred_queue: list[DeferredFK] = []
    dest_index = NKIndex()

    # Phase-1 (existing loop, threading snapshot_index and
    # deferred_queue through _resolve_body).
    for ct in _content_type_order(manifest, snapshot_dir):
        ...

    # Phase-2, new.
    summary.phase2 = run_phase2(
        http, deferred_queue,
        dest_index=dest_index, registry=registry,
    )
    return summary
```

```python
# src/nbsnap/import_cli.py

def run_import_cli(args):
    ...
    summary = run_import(http, args.in_dir, max_skew=max_skew, on_error=args.on_error)

    # Phase-1 block, existing.
    sys.stderr.write("# nbsnap import complete\n")
    sys.stderr.write(f"  preflight version skew: {summary.preflight.version_skew.name}\n")
    for outcome in (UpsertOutcome.CREATED, UpsertOutcome.UPDATED,
                    UpsertOutcome.NOOP, UpsertOutcome.FAILED):
        sys.stderr.write(f"  {outcome.value}: {summary.counts.get(outcome, 0)}\n")

    # Phase-2 block, new.
    if summary.phase2 is not None:
        sys.stderr.write("  phase2:\n")
        sys.stderr.write(f"    patched: {summary.phase2.counts.get('patched', 0)}\n")
        sys.stderr.write(f"    skipped: {summary.phase2.counts.get('skipped', 0)}\n")
        sys.stderr.write(f"    failed:  {summary.phase2.counts.get('failed', 0)}\n")
        if summary.phase2.failures:
            first = summary.phase2.failures[0]
            sys.stderr.write(f"    first phase2 failure: {first[1][:160]}\n")

    # Exit code: Phase-1 failures OR Phase-2 failures.
    if summary.failures or (
        summary.phase2 is not None and not summary.phase2.is_clean()
    ):
        return EXIT_ROW_FAILURES
    return EXIT_OK
```

#### Regression tests

```python
# tests/unit/test_import_phase2_wiring.py
from unittest.mock import patch

from nbsnap.import_.driver import run_import
from nbsnap.import_.phase2 import Phase2Summary


def test_phase2_summary_attached_to_import_summary(tmp_path):
    """run_import() returns an ImportSummary with a phase2 field."""
    # Stub everything except the structural shape.
    snap = _write_minimal_snapshot(tmp_path)
    with patch("nbsnap.import_.driver.run_phase2",
               return_value=Phase2Summary()) as p2:
        summary = run_import(
            http=_fake_http(),
            snapshot_dir=snap,
        )
    p2.assert_called_once()
    assert summary.phase2 is not None
```

#### Acceptance criteria

* `summary.phase2` is non-None after every `nbsnap import` run.
* The CLI's stderr summary includes a `phase2:` block.
* Exit code 2 fires for Phase-2 failures the same way it fires
  for Phase-1 failures.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36d] Polymorphic-FK ordering hints for the planner

#### Architectural specification

NetBox uses generic foreign keys for fields that can point at
several content types: cable terminations, IP-address
assignments, services, wireless links. The OpenAPI schema
expresses these as two paired fields — `<prefix>_object_type`
(a string) and `<prefix>_object_id` (an integer) — instead of a
direct `$ref`. The planner's static FK detection has no way to
tell, from the schema alone, that a Cable's `a_terminations`
items will end up pointing at `dcim.interface` at runtime.

Consequence: the planner emits `dcim.cable` and
`ipam.ipaddress` BEFORE `dcim.interface`, even though both
reference Interface. The `FEAT-36b` look-ahead resolver is the
runtime safety net; this ticket gets the common case right at
plan time so the safety net rarely fires.

The fix is a **curated table of polymorphic target hints**.
Each entry says "for this content type, this field can point
at this target". The graph builder reads the table after the
static FK edges land and adds synthetic edges that look like
nullable m2m FKs from the owner to each target. The planner
then sorts naturally; the synthetic edges are still
defer-eligible if a true cycle exists, so the existing
cycle-breaking machinery still works.

Hints are version-stamped because NetBox occasionally
restructures these. The reference NetBox docs:
* Cable terminations: `https://demo.netbox.dev/api/schema/swagger-ui/#/dcim/dcim_cables_list`,
  see `a_terminations` / `b_terminations` schema.
* IPAddress assigned object: same Swagger UI under
  `ipam_ip_addresses_list`, see `assigned_object_type` /
  `assigned_object_id`.
* Service assigned object: same idea, see `ipam_services_list`.
* WirelessLink endpoints A/B: see `wireless_wireless_links_list`.

#### REST API details

Run-time discovery of accepted target types uses
`OPTIONS /api/<endpoint>/`, which NetBox returns with the
`actions.POST.<field>.choices` array (see
`src/nbsnap/graph/polymorphic.py:discover_via_options`, already
landed in FEAT-05c1). The hint table is a **static fallback /
optimisation** so the planner does not need an OPTIONS round
trip to know that Cable's terminations target Interface.

For the curated entries, the verified NetBox 4.6 list is:

| Owner | Field | Likely targets |
| :--- | :--- | :--- |
| `dcim.cable` | `a_terminations`, `b_terminations` | `dcim.interface`, `dcim.frontport`, `dcim.rearport`, `dcim.consoleport`, `dcim.consoleserverport`, `dcim.powerport`, `dcim.poweroutlet`, `circuits.circuittermination` |
| `ipam.ipaddress` | `assigned_object` | `dcim.interface`, `virtualization.vminterface`, `ipam.fhrpgroup` |
| `ipam.service` | `parent` | `dcim.device`, `virtualization.virtualmachine`, `ipam.fhrpgroup` |
| `wireless.wirelesslink` | `interface_a`, `interface_b` | `dcim.interface` |
| `dcim.virtualchassis` | `master` | `dcim.device` |

#### Implementation

```python
# src/nbsnap/graph/polymorphic.py

# Hand-curated polymorphic target hints. Each entry says "this
# content type, via this field, can point at this target". The
# graph builder uses the table to add synthetic ordering edges
# so the planner emits the target content type BEFORE the
# owner. The OPTIONS-based discovery in discover_via_options()
# remains the runtime source of truth; this table is the cheap
# upfront optimisation that keeps the common case from
# round-tripping to NetBox.
#
# Each entry carries a `verified_against` tag so a NetBox bump
# that restructures these fields can be caught by re-running
# the hint check against the new schema.
POLYMORPHIC_HINTS: list[dict] = [
    {
        "owner_ct": "dcim.cable",
        "field": "a_terminations",
        "targets": [
            "dcim.interface", "dcim.frontport", "dcim.rearport",
            "dcim.consoleport", "dcim.consoleserverport",
            "dcim.powerport", "dcim.poweroutlet",
            "circuits.circuittermination",
        ],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "dcim.cable",
        "field": "b_terminations",
        "targets": [
            "dcim.interface", "dcim.frontport", "dcim.rearport",
            "dcim.consoleport", "dcim.consoleserverport",
            "dcim.powerport", "dcim.poweroutlet",
            "circuits.circuittermination",
        ],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "ipam.ipaddress",
        "field": "assigned_object",
        "targets": ["dcim.interface", "virtualization.vminterface", "ipam.fhrpgroup"],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "ipam.service",
        "field": "parent",
        "targets": ["dcim.device", "virtualization.virtualmachine", "ipam.fhrpgroup"],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "wireless.wirelesslink",
        "field": "interface_a",
        "targets": ["dcim.interface"],
        "verified_against": "netbox 4.6.2",
    },
    {
        "owner_ct": "wireless.wirelesslink",
        "field": "interface_b",
        "targets": ["dcim.interface"],
        "verified_against": "netbox 4.6.2",
    },
]


def add_hint_edges(graph, scope: set[str]) -> None:
    """Add synthetic FK edges from the polymorphic hint table.

    For each `(owner, field, target)` triple where both content
    types are in scope, add an edge child=owner -> parent=target,
    marked nullable + m2m so the planner can still defer it if a
    cycle exists. The label `field` is suffixed with `__hint` so
    the deferred-edge picker can recognise these and prefer
    deferring them over real schema edges.
    """
    from nbsnap.graph.model import Edge, Node

    for hint in POLYMORPHIC_HINTS:
        owner = hint["owner_ct"]
        if owner not in scope:
            continue
        for target in hint["targets"]:
            if target not in scope:
                continue
            graph.add_node(Node(target))  # idempotent
            graph.add_edge(
                Edge(
                    child=owner,
                    parent=target,
                    field=f"{hint['field']}__hint",
                    nullable=True,
                    required=False,
                    is_m2m=True,
                    polymorphic_targets=tuple(hint["targets"]),
                )
            )
```

```python
# src/nbsnap/graph/build.py

from nbsnap.graph.polymorphic import add_hint_edges

def from_openapi(openapi, scope: set[str]) -> Graph:
    graph = Graph()
    for content_type in sorted(scope):
        graph.add_node(Node(content_type))

    # Existing static FK extraction loop.
    for endpoint in sorted(openapi.iter_endpoints(), key=lambda e: e.path):
        ...

    # NEW: layer polymorphic hints on top.
    add_hint_edges(graph, scope)

    return graph
```

#### Regression test

```python
# tests/unit/test_graph_polymorphic_hints.py
from nbsnap.graph import from_openapi, plan
from nbsnap.graph.polymorphic import add_hint_edges
from nbsnap.schema.openapi import OpenAPI


def _minimal_schema_with_cable_and_interface() -> dict:
    """Schema with cable and interface, no direct FK between
    them (cable terminations are polymorphic). Without hints
    the planner has no reason to order interface before cable."""
    return {
        "paths": {
            "/api/dcim/interfaces/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {"properties": {"id": {}, "name": {}}}}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {"properties": {"name": {}}}}
                }}},
            },
            "/api/dcim/cables/": {
                "get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {"properties": {"id": {}}}}
                }}}},
                "post": {"requestBody": {"content": {
                    "application/json": {"schema": {"properties": {}}}
                }}},
            },
        }
    }


def test_hints_put_interface_before_cable() -> None:
    """With the polymorphic hint table, dcim.interface lands in
    the topo order before dcim.cable."""
    openapi = OpenAPI(_minimal_schema_with_cable_and_interface())
    graph = from_openapi(openapi, scope={"dcim.interface", "dcim.cable"})
    p = plan(graph)
    assert p.order.index("dcim.interface") < p.order.index("dcim.cable")


def test_hints_skipped_when_target_out_of_scope() -> None:
    """Hint edges only land when both endpoints are in scope."""
    openapi = OpenAPI(_minimal_schema_with_cable_and_interface())
    # Cable in scope, interface NOT in scope. Hint must be dropped.
    graph = from_openapi(openapi, scope={"dcim.cable"})
    # No synthetic edge from cable to a non-existent interface node.
    from nbsnap.graph.model import Node
    out = graph.out_edges(Node("dcim.cable"))
    assert all(e.parent != "dcim.interface" for e in out)
```

#### Acceptance criteria

* After the hints land, the planner against the production
  schema emits `dcim.interface` before `dcim.cable` and before
  `ipam.ipaddress`.
* The `FEAT-36b` look-ahead resolver no longer fires for cable
  terminations or IPAddress assigned_object in the common case,
  measurable by a near-empty `deferred_queue` on the
  renderer-minimum fixture.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36e] Out-of-scope vs missing-in-snapshot audit split

#### Architectural specification

Today every FK drop emits the same warning shape:

    dropping FK <content_type>.<field> -> <target_ct>,
    NK <nk> not found on destination

After FEAT-36a/b/c, the resolver has more information about
**why** a target is missing. Three distinct categories deserve
different operator attention:

1. **OUT_OF_SCOPE.** The target content type is not carried by
   the snapshot (e.g. `dcim.region`, `ipam.vrf`,
   `virtualization.*`). This is by design, the network-only
   scope banner in `CLAUDE.md` excludes these. The operator
   does not need to act.
2. **MISSING_FROM_SOURCE.** The target IS in scope, but the NK
   the source's record references does not exist anywhere — not
   on the destination, not in the snapshot. The source NetBox
   has a stale or broken reference. Worth a one-line audit
   entry the operator can grep.
3. **DEFERRED_TO_PHASE2.** The target is in a cycle with the
   current record. Phase-2 will PATCH it in. Not an error,
   just visibility.

Two outputs:

* **stderr summary** at the end of the run, a per-category
  count and the top offending `(content_type, field)` pairs.
  Quick read at the terminal.
* **audit.jsonl** under the snapshot directory, one JSON object
  per drop event. Greppable, parseable, archivable.

This ticket replaces the noisy "warn every drop" path with a
single accumulator that classifies and de-duplicates.

#### REST API details

None directly. This is an audit/observability ticket; no
NetBox calls happen here.

For context, the resolver decisions that feed the audit come
from these NetBox endpoints, all read-only and already used:

* `GET /api/<endpoint>/?brief=true` (NKIndex build, decides
  whether the target is on the destination).
* The snapshot files on disk (decide whether the target is in
  the snapshot).

#### Implementation

```python
# src/nbsnap/import_/audit.py
"""Categorised drop / defer audit for the import resolver.

Three categories: OUT_OF_SCOPE, MISSING_FROM_SOURCE,
DEFERRED_TO_PHASE2. Each is recorded once per
(content_type, field, target_content_type, target_nk) tuple so
log volume stays sane even on a huge import. End-of-run
summary slices the totals by category.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DropCategory(Enum):
    """Why an FK reference did not make it to the destination."""

    OUT_OF_SCOPE = "out_of_scope"
    MISSING_FROM_SOURCE = "missing_from_source"
    DEFERRED_TO_PHASE2 = "deferred_to_phase2"


@dataclass
class DropEvent:
    """One categorised FK drop, written to audit.jsonl."""

    category: DropCategory
    child_content_type: str
    child_nk: tuple
    field_name: str
    target_content_type: str
    target_nk: tuple
    message: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "child": {"content_type": self.child_content_type, "nk": list(self.child_nk)},
            "field": self.field_name,
            "target": {"content_type": self.target_content_type, "nk": list(self.target_nk)},
            "message": self.message,
        }


@dataclass
class Auditor:
    """Accumulates drop events and renders the summary."""

    events: list[DropEvent] = field(default_factory=list)
    _seen: set[tuple] = field(default_factory=set)

    def record(self, event: DropEvent) -> None:
        """Record one event; de-dupes on (ct, field, target_ct, target_nk)."""
        key = (
            event.child_content_type, event.field_name,
            event.target_content_type, event.target_nk,
        )
        if key in self._seen:
            return
        self._seen.add(key)
        self.events.append(event)
        logger.info(
            "[%s] %s.%s -> %s NK=%r",
            event.category.value,
            event.child_content_type, event.field_name,
            event.target_content_type, event.target_nk,
        )

    def write_jsonl(self, path: Path) -> None:
        """Persist the full event list to disk, one JSON object per line."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            for event in self.events:
                fp.write(json.dumps(event.to_json(), sort_keys=True) + "\n")

    def render_summary(self) -> str:
        """Build the stderr-friendly summary table."""
        if not self.events:
            return "  audit: no FK drops or defers.\n"
        by_category: Counter[str] = Counter(e.category.value for e in self.events)
        by_pair: defaultdict[tuple[str, str], int] = defaultdict(int)
        for ev in self.events:
            by_pair[(ev.child_content_type, ev.field_name)] += 1

        out = ["  audit:"]
        for cat in DropCategory:
            count = by_category.get(cat.value, 0)
            out.append(f"    {cat.value}: {count}")
        # Top 5 offenders.
        top = sorted(by_pair.items(), key=lambda kv: -kv[1])[:5]
        out.append("    top offending (content_type, field):")
        for (ct, fld), n in top:
            out.append(f"      {ct}.{fld}: {n}")
        return "\n".join(out) + "\n"
```

```python
# src/nbsnap/import_/driver.py

from nbsnap.import_.audit import Auditor, DropCategory, DropEvent

def run_import(http, snapshot_dir, *, ...):
    ...
    auditor = Auditor()
    # Thread auditor through _resolve_body so every drop sees it.
    summary.auditor = auditor
    ...

def _resolve_body(content_type, body, openapi, dest_index, http, registry, *,
                  snapshot_index, deferred_queue, auditor, **kw):
    ...
    if rid is None:
        # Determine which bucket.
        if not snapshot_index.has(spec.fk_target, target_nk):
            category = DropCategory.MISSING_FROM_SOURCE
        elif _was_just_deferred(spec.fk_target, target_nk, deferred_queue):
            category = DropCategory.DEFERRED_TO_PHASE2
        else:
            category = DropCategory.OUT_OF_SCOPE
        auditor.record(DropEvent(
            category=category,
            child_content_type=content_type,
            child_nk=current_nk,
            field_name=field_name,
            target_content_type=spec.fk_target,
            target_nk=target_nk,
        ))
```

CLI hook in `src/nbsnap/import_cli.py` after the existing
summary block:

```python
def add_import_args(parser):
    parser.add_argument(
        "--audit-out", type=Path, default=None,
        help="write a per-drop audit.jsonl to this path "
             "(default: <snapshot_dir>/audit.jsonl)",
    )

def run_import_cli(args):
    ...
    sys.stderr.write(summary.auditor.render_summary())
    audit_path = args.audit_out or (args.in_dir / "audit.jsonl")
    summary.auditor.write_jsonl(audit_path)
    sys.stderr.write(f"  audit log: {audit_path}\n")
```

#### Regression test

```python
# tests/unit/test_import_audit_split.py
import json

from nbsnap.import_.audit import Auditor, DropCategory, DropEvent


def test_record_deduplicates_on_quadruple_key() -> None:
    a = Auditor()
    ev = DropEvent(
        category=DropCategory.OUT_OF_SCOPE,
        child_content_type="dcim.site",
        child_nk=("hall-d",),
        field_name="region",
        target_content_type="dcim.region",
        target_nk=("elmia",),
    )
    a.record(ev)
    a.record(ev)  # duplicate
    a.record(ev)  # duplicate
    assert len(a.events) == 1


def test_summary_counts_by_category() -> None:
    a = Auditor()
    a.record(DropEvent(
        DropCategory.OUT_OF_SCOPE, "dcim.site", ("a",),
        "region", "dcim.region", ("elmia",),
    ))
    a.record(DropEvent(
        DropCategory.MISSING_FROM_SOURCE, "dcim.device", ("d39a",),
        "platform", "dcim.platform", ("ghost",),
    ))
    a.record(DropEvent(
        DropCategory.DEFERRED_TO_PHASE2, "dcim.device", ("d39a",),
        "primary_ip4", "ipam.ipaddress", ("172.16.1.10/24",),
    ))
    text = a.render_summary()
    assert "out_of_scope: 1" in text
    assert "missing_from_source: 1" in text
    assert "deferred_to_phase2: 1" in text


def test_jsonl_roundtrip(tmp_path) -> None:
    a = Auditor()
    a.record(DropEvent(
        DropCategory.OUT_OF_SCOPE, "dcim.site", ("a",),
        "region", "dcim.region", ("elmia",),
    ))
    path = tmp_path / "audit.jsonl"
    a.write_jsonl(path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["category"] == "out_of_scope"
    assert rows[0]["target"] == {"content_type": "dcim.region", "nk": ["elmia"]}
```

#### Acceptance criteria

* After a clean import, the operator sees a one-line summary
  per category (out_of_scope, missing_from_source,
  deferred_to_phase2) and the top 5 (content_type, field)
  offenders.
* `audit.jsonl` is created next to the snapshot or at the path
  passed via `--audit-out`.
* Duplicate drops do not bloat the log; the de-dupe key is
  `(child_ct, field, target_ct, target_nk)`.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36f] `nbsnap import` is single-pass by default

#### Architectural specification

This is the **milestone ticket** for the FEAT-36 series. It
adds no new behaviour; it asserts and documents that the work
done in FEAT-36-blocker through FEAT-36e is enough to make a
single `nbsnap import` invocation produce a complete
destination NetBox from any well-formed snapshot, regardless of
plan order.

After FEAT-36-blocker (enum-dict fix), FEAT-36a (snapshot
index), FEAT-36b (demand-driven resolver), FEAT-36c (Phase-2
wiring), FEAT-36d (polymorphic ordering hints), and FEAT-36e
(categorised audit) all land, the workaround documented in
the README and the rescue notes ("if the first import shows
failures, run it again") becomes obsolete.

The exit-code contract sharpens. `EXIT_ROW_FAILURES` (2) now
means **real failures**, not "expected ordering noise". The
three categories from FEAT-36e are kept separate in the
exit-code logic:

* `OUT_OF_SCOPE` drops do NOT contribute to exit 2; they are
  documented behaviour, the network-only scope banner is
  explicit about excluding these.
* `DEFERRED_TO_PHASE2` does NOT contribute either; Phase-2's
  per-edge PATCH outcome is what matters.
* `MISSING_FROM_SOURCE` does, because the source has a stale
  reference. The operator should investigate.
* Phase-2 PATCH failures contribute, same logic.

#### REST API details

None new. This ticket only changes contract / documentation /
test assertions. The underlying NetBox calls are the same that
FEAT-36b and FEAT-36c (FEAT-23) describe.

#### Implementation

```python
# src/nbsnap/import_cli.py
"""Exit-code logic now considers Phase-2 and audit categories."""

from nbsnap.import_.audit import DropCategory


def run_import_cli(args) -> int:
    summary = run_import(http, args.in_dir, ...)

    # Render Phase-1 + Phase-2 + audit blocks (as before).

    # NEW exit logic.
    return _compute_exit_code(summary)


def _compute_exit_code(summary) -> int:
    """Map a fully-categorised ImportSummary to a CLI exit code.

    Returns:
        EXIT_OK on a clean run.
        EXIT_ROW_FAILURES when any of:
          - Phase-1 had upsert failures
          - Phase-2 had PATCH failures
          - the audit log carries any MISSING_FROM_SOURCE drops
        EXIT_PREFLIGHT_BLOCKED on pre-flight findings.
        EXIT_DESTINATION_UNREACHABLE on TLS / 401 / 5xx, already
            mapped by the early try/except.

    Out-of-scope and deferred-to-phase-2 drops do NOT contribute
    to the exit code, per FEAT-36f contract.
    """
    if summary.preflight.is_blocking(args_max_skew):
        return EXIT_PREFLIGHT_BLOCKED

    real_failures = (
        len(summary.failures)
        + (
            summary.phase2.counts.get("failed", 0)
            if summary.phase2 is not None else 0
        )
    )
    missing_from_source = sum(
        1 for ev in summary.auditor.events
        if ev.category is DropCategory.MISSING_FROM_SOURCE
    )

    if real_failures or missing_from_source:
        return EXIT_ROW_FAILURES
    return EXIT_OK
```

Documentation updates needed:

1. **README.md**, Development section, remove the "run a
   second pass" caveat.
2. **`docs/05-export-import-workflow.md`**, update Phase I3 to
   describe single-pass operation as the normal case.
3. **`docs/01-scope.md`** or a new operator-facing doc,
   formalise the "out-of-scope drops are expected, missing-from-
   source drops require action" distinction.

#### Integration test

```python
# tests/integration/test_idempotency.py (TEST-06, updated)
"""Two-run idempotency, second run is a pure NOOP."""

import pytest

from nbsnap.export.driver import run_export
from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.driver import run_import
from nbsnap.import_.audit import DropCategory
from nbsnap.import_.upsert import UpsertOutcome
from nbsnap.schema.status import VersionSkew


@pytest.mark.usefixtures("require_stack")
def test_single_pass_import_is_complete(tmp_path) -> None:
    """One nbsnap import call against a clean destination
    produces a destination NetBox that matches the source.
    No 'run it twice' required."""

    src = NetboxHTTP("http://localhost:8080", "0123456789abcdef" * 2 + "01234567")
    dst = NetboxHTTP("http://localhost:8081", "abcdef0123456789" * 2 + "abcdef01")
    snap_dir = tmp_path / "snap"
    run_export(src, snap_dir)

    summary = run_import(dst, snap_dir, max_skew=VersionSkew.MINOR, on_error="continue")

    # No real failures.
    assert len(summary.failures) == 0, [f.message for f in summary.failures]
    assert summary.phase2 is None or summary.phase2.is_clean()

    # No MISSING_FROM_SOURCE drops on a healthy source.
    missing = [
        ev for ev in summary.auditor.events
        if ev.category is DropCategory.MISSING_FROM_SOURCE
    ]
    assert missing == [], [
        f"{ev.child_content_type}.{ev.field_name} -> {ev.target_content_type}"
        for ev in missing
    ]


@pytest.mark.usefixtures("require_stack")
def test_second_run_is_noop(tmp_path) -> None:
    """The hallmark of true idempotency."""

    src = NetboxHTTP("http://localhost:8080", "0123456789abcdef" * 2 + "01234567")
    dst = NetboxHTTP("http://localhost:8081", "abcdef0123456789" * 2 + "abcdef01")
    snap_dir = tmp_path / "snap"
    run_export(src, snap_dir)
    run_import(dst, snap_dir, max_skew=VersionSkew.MINOR, on_error="continue")

    # Second run.
    summary2 = run_import(dst, snap_dir, max_skew=VersionSkew.MINOR, on_error="continue")
    # Every record is NOOP, nothing created, nothing updated.
    assert summary2.counts.get(UpsertOutcome.CREATED, 0) == 0
    assert summary2.counts.get(UpsertOutcome.UPDATED, 0) == 0
    # Phase-2 also a no-op.
    assert summary2.phase2 is None or summary2.phase2.counts.get("patched", 0) == 0
```

#### Acceptance criteria

* `nbsnap import --in <fresh snapshot> --on-error continue`
  against an **empty** destination returns exit 0 with
  `failed: 0` for the renderer-minimum fixture.
* A second invocation of the same command returns exit 0 with
  `created: 0, updated: 0, noop: <total>` and a Phase-2 block
  showing `patched: 0`.
* The README no longer mentions "run twice" or the equivalent
  workaround; `docs/05-export-import-workflow.md` describes
  single-pass as the normal mode.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36g] Only defer nullable FKs that actually participate in a cycle

#### Architectural specification

The planner's existing `pick_deferred_edges` (in
`src/nbsnap/graph/algo.py`) returns every nullable or m2m edge
inside a size-1 SCC as deferrable, plus the alphabetically-
earliest qualifying edge in larger SCCs. The size-1 case fires
on **self-edges** (e.g. `dcim.devicerole.parent → dcim.devicerole`),
where it correctly identifies the only way to break a self-loop.
The bug is that the helper ignores edges that don't participate
in any SCC at all.

Walking the production schema reveals the impact:

* `ipam.prefix.vlan` is nullable, and `ipam.vlan` never points
  back at `ipam.prefix`. There is no SCC; the edge is on a
  simple DAG between two distinct content types. Today the
  planner deferred this edge anyway (the size-1 check fired on
  prefix's SCC of size 1 and the helper found the nullable
  edge), allowing `ipam.prefix` to land at plan position 2,
  before `ipam.vlan` at position 13. The prefix POSTs land
  with `vlan: null`, the planner never fixes it because
  Phase-2 only consults the explicit deferred queue (and the
  look-ahead resolver in FEAT-36b would have to fire on every
  prefix to backfill the vlan).

The fix is to **let Tarjan tell us which edges genuinely close
cycles**. Tarjan's SCC pass already runs (see
`strongly_connected_components`). An edge whose endpoints fall
in DIFFERENT SCCs is on the DAG between SCCs; deferring it is
wasteful. An edge whose endpoints fall in the SAME SCC of size
≥ 2 closes a cycle and is a real deferrable candidate. An edge
whose endpoints are the SAME node (self-edge) is also a real
deferrable.

#### REST API details

None. This is a pure planner change inside
`src/nbsnap/graph/algo.py`. It only affects what order the
import driver feeds content types to NetBox; the NetBox calls
themselves stay the same.

The downstream effect on the REST API is that fewer
`GET ?brief=true` calls fire from the demand-driven resolver
(FEAT-36b), because the static plan already gets the order
right for the prefix → vlan case. Roughly N fewer GETs where N
is the number of misordered nullable FKs (typically a handful
per scope).

#### Implementation

```python
# src/nbsnap/graph/algo.py

def pick_deferred_edges(graph: Graph, scc: list[Node]) -> list[Edge]:
    """Defer only edges that genuinely close a cycle.

    Three rules in priority order:

    1. Self-edge (size-1 SCC where the node points at itself).
       Always defer every eligible (nullable or m2m) self-edge.
       This is the existing FEAT-06b behaviour.
    2. Edge inside a size>1 SCC where BOTH endpoints are
       members of the SCC. Defer the cheapest eligible one
       (sorted by nullable-first, then m2m-first, then alpha).
    3. Edge whose endpoints sit in different SCCs (or whose
       parent is outside the scc list entirely): NEVER defer.
       The edge is on the DAG between SCCs and the planner
       can order around it without trouble.

    Rule 3 is the fix; the previous implementation treated
    size-1 SCCs as if every nullable edge they emitted was a
    self-edge, which is true for self-loops but false for
    edges into other SCCs.
    """
    scc_set = set(scc)

    # Rule 1, self-edges in a size-1 SCC.
    if len(scc) == 1:
        node = scc[0]
        deferred: list[Edge] = []
        for edge in graph.out_edges(node):
            if Node(edge.parent) == node and (edge.nullable or edge.is_m2m):
                deferred.append(edge)
        # If the node has no self-edges, do NOT defer any of its
        # outgoing edges (they all go to other SCCs).
        return deferred

    # Rule 2, edges that close a cycle inside this SCC.
    candidates: list[Edge] = []
    for node in sorted(scc, key=lambda n: n.content_type):
        for edge in graph.out_edges(node):
            if Node(edge.parent) not in scc_set:
                continue  # Rule 3: edge leaves the SCC, don't defer.
            if edge.nullable or edge.is_m2m:
                candidates.append(edge)

    if not candidates:
        return []
    candidates.sort(
        key=lambda e: (not e.nullable, not e.is_m2m, e.child, e.field, e.parent),
    )
    return [candidates[0]]
```

The Tarjan output already returns each SCC as a `list[Node]`.
`strongly_connected_components` returns them in reverse-topo
order; the `plan()` driver iterates each in turn and calls
`pick_deferred_edges`, so the new rule applies automatically
without any structural change in the caller.

#### Regression tests

```python
# tests/unit/test_graph_no_unnecessary_defer.py
from nbsnap.graph.algo import pick_deferred_edges, plan, strongly_connected_components
from nbsnap.graph.model import Edge, Graph, Node


def _edge(child, parent, *, nullable=False, m2m=False) -> Edge:
    return Edge(
        child=child, parent=parent, field="x",
        nullable=nullable, required=not nullable, is_m2m=m2m,
    )


def _graph_with(edges) -> Graph:
    g = Graph()
    for e in edges:
        g.add_node(Node(e.child))
        g.add_node(Node(e.parent))
    for e in edges:
        g.add_edge(e)
    return g


def test_acyclic_nullable_edge_is_not_deferred() -> None:
    """A nullable edge A.b -> B with no return edge is on the
    DAG, not deferred. B lands before A in the topo order."""
    g = _graph_with([_edge("ipam.prefix", "ipam.vlan", nullable=True)])
    p = plan(g)
    assert p.deferred == []
    assert p.order.index("ipam.vlan") < p.order.index("ipam.prefix")


def test_self_loop_is_still_deferred() -> None:
    """Self-loops (role.parent -> role) keep their deferred
    behaviour."""
    g = _graph_with([_edge("dcim.devicerole", "dcim.devicerole", nullable=True)])
    p = plan(g)
    assert len(p.deferred) == 1
    assert p.deferred[0].child == "dcim.devicerole"


def test_two_node_cycle_defers_one_edge() -> None:
    """Real cycle A <-> B: pick_deferred_edges picks the
    nullable side."""
    g = _graph_with([
        _edge("A", "B", nullable=True),
        _edge("B", "A"),
    ])
    sccs = strongly_connected_components(g)
    scc = next(s for s in sccs if len(s) == 2)
    deferred = pick_deferred_edges(g, scc)
    assert len(deferred) == 1
    assert deferred[0].child == "A"  # nullable side wins
```

Plus a fixture-driven test that loads a representative NetBox
4.6 schema dump from `tests/fixtures/openapi-prod-4.6.json`
(extract once via `nbsnap export --url <prod> --no-verify-tls`
and copy the resulting `snapshot/schema/openapi.json`):

```python
def test_planner_orders_vlan_before_prefix_on_production_schema() -> None:
    from nbsnap.schema.openapi import OpenAPI
    from nbsnap.graph import from_openapi
    openapi = OpenAPI.load("tests/fixtures/openapi-prod-4.6.json")
    scope = {"ipam.vlan", "ipam.prefix", "ipam.iprange", "ipam.ipaddress"}
    p = plan(from_openapi(openapi, scope=scope))
    assert p.order.index("ipam.vlan") < p.order.index("ipam.prefix")
    assert p.order.index("ipam.vlan") < p.order.index("ipam.iprange")
```

#### Acceptance criteria

* The plan order against the production schema places
  `ipam.vlan` before `ipam.prefix`, `ipam.iprange`, and
  `ipam.ipaddress`.
* `Plan.deferred` no longer contains entries for nullable FKs
  that have no return path.
* Existing self-loop defers (DeviceRole.parent, Platform.parent,
  Location.parent, Device.parent_device, VLAN.qinq_svlan,
  IPAddress.nat_inside / nat_outside) still appear in
  `Plan.deferred` and still get PATCHed by Phase-2.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36h] Fail-fast preflight on enum-dict-shaped snapshots

#### Architectural specification

`FEAT-36-blocker` is the canonical fix on the export side, and
the import side already has the defensive `_collapse_enum_dict`
coerce. But the user's experience matters: a pre-blocker
snapshot driven into a live import today produces 5000+ "FK not
found" warnings, hides the root cause inside the cascade, and
costs an operator real time before they realise the snapshot
itself is bad.

This ticket adds an **early failure path** in the import
preflight that spots the enum-dict pattern at the boundary,
flags the snapshot as known-bad, and aborts with a clear "this
snapshot needs to be re-exported with nbsnap ≥ <version>"
message. Costs ~10 ms because we sample only the first row of
each jsonl file.

The check coexists with FEAT-36-blocker's coerce. After the
coerce lands, properly-fixed snapshots pass the preflight
cleanly; legacy snapshots that the coerce would still rescue
get either:

* a friendly halt (the default, so the operator knows to
  re-export); OR
* a forced-bypass via `--allow-enum-dict-bypass`, which keeps
  the legacy import path alive while a re-export is being
  staged. The bypass is a power-user escape hatch documented
  but not advertised.

#### REST API details

None. This check is purely on-disk; it reads JSONL bytes only.

For context, the failure mode this check averts:

    POST /api/dcim/sites/
    Content-Type: application/json
    Authorization: Token <token>

    {"name": "Hall-D", "slug": "hall-d",
     "status": {"value": "active", "label": "Active"}}

    -> HTTP 400
    {"status": ["Value must be passed directly (e.g. \"foo\":
                 123); do not use a dictionary or list."]}

NetBox documentation reference:
`https://netboxlabs.com/docs/netbox/integrations/rest-api/`
under "Choice fields, request vs response shape" — NetBox is
explicit that writes accept only the bare value.

#### Implementation

```python
# src/nbsnap/import_/preflight.py

from __future__ import annotations

import json
from dataclasses import field
from pathlib import Path


# Add this constant to the existing module.
_ENUM_DICT_KEYS = frozenset({"value", "label"})

# Bytes read per file when sampling. The first row of a jsonl
# is always smaller than this for the renderer-minimum scope,
# so we capture it whole and short-circuit on the newline.
_SAMPLE_BYTES = 4096


def sample_enum_dict_check(snapshot_dir: Path) -> list[str]:
    """Return a list of jsonl files whose first row carries the
    enum-dict pattern. Each entry in the returned list names
    the file and the offending field, so the operator's error
    message can point at the exact location."""

    issues: list[str] = []
    for jsonl in sorted(snapshot_dir.rglob("*.jsonl")):
        if jsonl.name in {"flags.jsonl", "progress.jsonl",
                          "_deferred.jsonl", "audit.jsonl"}:
            continue
        with jsonl.open(encoding="utf-8") as fp:
            line = fp.readline(_SAMPLE_BYTES)
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        body = row.get("body") or {}
        for field_name, value in body.items():
            if (
                isinstance(value, dict)
                and frozenset(value.keys()) == _ENUM_DICT_KEYS
            ):
                rel = jsonl.relative_to(snapshot_dir).as_posix()
                issues.append(
                    f"{rel}: field {field_name!r} carries the "
                    f"{{value, label}} enum-dict shape; the snapshot "
                    f"was exported before FEAT-36-blocker landed"
                )
                break  # one issue per file is plenty
    return issues
```

Extend `PreflightReport`:

```python
# src/nbsnap/import_/preflight.py
@dataclass
class PreflightReport:
    version_skew: VersionSkew = VersionSkew.NONE
    missing_content_types: set[str] = field(default_factory=set)
    missing_custom_fields: set[str] = field(default_factory=set)
    snapshot_format_version: int = 1
    # NEW
    snapshot_format_issues: list[str] = field(default_factory=list)

    def is_blocking(self, max_skew: VersionSkew, *, allow_enum_dict_bypass: bool = False) -> bool:
        if self.missing_content_types:
            return True
        if self.snapshot_format_issues and not allow_enum_dict_bypass:
            return True
        return not self.version_skew.allowed_by(max_skew)
```

Wire the check into `run_preflight`:

```python
def run_preflight(http, manifest, *, custom_field_names=None, snapshot_dir=None):
    report = PreflightReport(snapshot_format_version=manifest.version)
    ...
    if snapshot_dir is not None:
        report.snapshot_format_issues = sample_enum_dict_check(snapshot_dir)
    return report
```

CLI surface:

```python
# src/nbsnap/import_cli.py
def add_import_args(parser):
    parser.add_argument(
        "--allow-enum-dict-bypass",
        action="store_true",
        help="(power user) proceed even if the snapshot carries "
             "the legacy {value,label} enum shape on a field. The "
             "import-side coerce should still recover, but the "
             "snapshot will not round-trip cleanly.",
    )

def run_import_cli(args):
    ...
    if summary.preflight.snapshot_format_issues:
        sys.stderr.write(
            "nbsnap import: snapshot format issues detected:\n"
        )
        for issue in summary.preflight.snapshot_format_issues[:10]:
            sys.stderr.write(f"  {issue}\n")
        sys.stderr.write(
            "Re-export this snapshot with nbsnap >= <version> "
            "to fix, or pass --allow-enum-dict-bypass to proceed "
            "via the import-side coerce.\n"
        )
```

#### Regression test

```python
# tests/unit/test_preflight_enum_dict.py
import json
from pathlib import Path

from nbsnap.import_.preflight import sample_enum_dict_check


def test_enum_dict_in_first_row_is_flagged(tmp_path: Path) -> None:
    sites = tmp_path / "dcim/sites.jsonl"
    sites.parent.mkdir()
    sites.write_text(json.dumps({
        "natural_key": ["hall-a"],
        "body": {"name": "Hall-A", "slug": "a",
                 "status": {"value": "active", "label": "Active"}},
    }) + "\n", encoding="utf-8")

    issues = sample_enum_dict_check(tmp_path)
    assert len(issues) == 1
    assert "dcim/sites.jsonl" in issues[0]
    assert "status" in issues[0]


def test_clean_snapshot_returns_no_issues(tmp_path: Path) -> None:
    sites = tmp_path / "dcim/sites.jsonl"
    sites.parent.mkdir()
    sites.write_text(json.dumps({
        "natural_key": ["hall-a"],
        "body": {"name": "Hall-A", "slug": "a", "status": "active"},
    }) + "\n", encoding="utf-8")

    assert sample_enum_dict_check(tmp_path) == []


def test_audit_files_are_skipped(tmp_path: Path) -> None:
    """flags.jsonl etc. are not records, must not be sampled."""
    flags = tmp_path / "flags.jsonl"
    flags.write_text(json.dumps({
        "content_type": "ipam.ipaddress",
        "natural_key": [], "field": "dns_name", "reason": "...",
    }) + "\n", encoding="utf-8")

    assert sample_enum_dict_check(tmp_path) == []
```

#### Acceptance criteria

* `sample_enum_dict_check` on a snapshot produced after
  FEAT-36-blocker returns `[]`.
* `sample_enum_dict_check` on the rescue-10 snapshot (saved as
  a fixture in `tests/fixtures/legacy-enum-dict-snapshot/`)
  returns at least one entry per content type that has a
  status / airflow / type field.
* The CLI's exit code 1 (`EXIT_PREFLIGHT_BLOCKED`) fires on
  the legacy snapshot unless `--allow-enum-dict-bypass` is
  given.
* The error message points at one specific file/field so the
  operator can verify with `head -1` and `jq`.

**Estimated Effort:** 1-2h

### [REFINED] [FEAT-36i] NKIndex builds its NK dependencies recursively

#### Architectural specification

Composite NK specs reference other content types via the
`NKField.parent_content_type` field. Today's
`NKIndex.ensure_built(http, registry, content_type)` builds the
index for just the named content type — it does not walk the
NKSpec's dependencies. For shallow NKs (slug-only, single
parent) this is harmless because the recursive `resolve(...)`
call inside the NK resolver falls back to the nested
representation NetBox returns.

For deeply-nested NKs the fallback is not enough. Example from
the rescue log:

    dropping FK dcim.device.oob_ip -> ipam.ipaddress,
            NK ('172.31.255.100/24', 'dcim.interface',
                ((('d',), 'esxi0.infra.glitched.se'), 'ipmi'))
            not found on destination

The IPAddress's NK is composite:
`(address, assigned_object_type, assigned_object_id)`. The id
slot is the Interface's NK, which is itself composite
`(device, name)`, and the device slot is the Device's NK
`(site, name)`. Three nested levels.

For an IPAddress NK lookup to hit on the destination, the
NKIndex needs the IPAddress index, **and** the Interface index
(to compute interface NKs during IPAddress NK resolution),
**and** the Device index (to compute device NKs for those
interfaces). Building only `ipam.ipaddress` leaves the deeper
levels empty, the resolver falls through to the Brief
representation NetBox sends with `?brief=true` (which lacks the
parent fields), and the NK comes out with `None` slots that
never match the snapshot's NK.

The fix: `ensure_built` walks the NKSpec's
`parent_content_type` references and builds them first, with a
cycle-safe `building: set[str]` parameter so a self-referencing
NKSpec (e.g. `dcim.devicerole` parent → devicerole) does not
loop. Same pattern Python's `graphlib.TopologicalSorter` uses,
but at runtime against NetBox.

#### REST API details

Each recursive level issues one paginated list call:

    GET /api/<endpoint>/?brief=true&limit=500
    Authorization: Token <token>

Following the `next` link until exhausted. For Interface,
device, site we are talking ~3 list calls per IPAddress-style
NK chain, all `?brief=true` so payloads stay small. NetBox's
`brief=true` is documented at
`https://netboxlabs.com/docs/netbox/integrations/rest-api/`
under "Brief Format".

Total GETs added by this change: bounded by the depth of the
deepest NKSpec in `default_registry`. For the renderer-minimum
scope, the deepest is IPAddress at three levels (Interface ->
Device -> Site). Maximum ~3 list calls extra per import run
(memoised after the first call).

#### Implementation

```python
# src/nbsnap/import_/nk_index.py
"""NKIndex with recursive build via NKSpec.parent_content_type."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from nbsnap.http.client import NetboxHTTP
from nbsnap.natkey.model import NKRegistry, NKSpec
from nbsnap.natkey.resolver import NaturalKey, resolve
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS

logger = logging.getLogger(__name__)


@dataclass
class NKIndex:
    _by_key: dict[tuple[str, NaturalKey], int] = field(default_factory=dict)
    _built_cts: set[str] = field(default_factory=set)

    def ensure_built(
        self,
        http: NetboxHTTP,
        registry: NKRegistry,
        content_type: str,
        *,
        _building: set[str] | None = None,
    ) -> None:
        """Populate the index for `content_type`, recursively
        building every content type its NKSpec references.

        The `_building` parameter is the active recursion stack;
        a content type already on the stack is skipped (this is
        how we tolerate self-referencing NKSpecs like
        `dcim.devicerole` with a `parent: dcim.devicerole`
        field).

        After this returns, every NK lookup against this
        content type has access to the parent indices it needs
        to resolve its NKSpec.
        """
        if content_type in self._built_cts:
            return
        if _building is None:
            _building = set()
        if content_type in _building:
            # Cycle, skip; the partial NK we can compute is the
            # best we can do without recursing forever.
            return
        _building.add(content_type)

        # Walk the NKSpec's parent dependencies first.
        if registry.has(content_type):
            spec: NKSpec = registry.get(content_type)
            for field_spec in spec.fields:
                if field_spec.parent_content_type is not None:
                    self.ensure_built(
                        http, registry, field_spec.parent_content_type,
                        _building=_building,
                    )

        # Now build this content type.
        endpoint = CONTENT_TYPE_ENDPOINTS.get(content_type)
        if endpoint is not None:
            sep = "&" if "?" in endpoint else "?"
            for row in http.get_all(f"{endpoint}{sep}brief=true"):
                try:
                    nk = resolve(registry, content_type, row)
                except (KeyError, ValueError):
                    continue
                rid = row.get("id")
                if isinstance(rid, int):
                    self._by_key[(content_type, nk)] = rid

        self._built_cts.add(content_type)
        _building.discard(content_type)

    def lookup(
        self, content_type: str, nk: NaturalKey
    ) -> int | None:
        return self._by_key.get((content_type, nk))

    def insert(
        self, content_type: str, nk: NaturalKey, destination_id: int
    ) -> None:
        self._by_key[(content_type, nk)] = destination_id

    def __len__(self) -> int:
        return len(self._by_key)

    def all_for_content_type(self, content_type: str) -> Mapping[NaturalKey, int]:
        return {nk: i for (ct, nk), i in self._by_key.items() if ct == content_type}
```

#### Regression test

```python
# tests/unit/test_nk_index_recursive_build.py
from unittest.mock import MagicMock

from nbsnap.import_.nk_index import NKIndex
from nbsnap.natkey.registry import default as default_registry


def test_building_ipaddress_first_builds_interface_then_device() -> None:
    """The order of GET calls when asked to build IPAddress's
    index respects the NK dependency chain: Device first, then
    Interface, then IPAddress."""

    http = MagicMock()
    call_order: list[str] = []

    def fake_get_all(endpoint: str):
        call_order.append(endpoint.split("?")[0])
        return iter([])  # empty pages, we are only watching the order

    http.get_all.side_effect = fake_get_all

    idx = NKIndex()
    idx.ensure_built(http, default_registry(), "ipam.ipaddress")

    # ipam.ipaddress NKSpec fields: address, assigned_object_type,
    # assigned_object_id. None of those have parent_content_type,
    # so the recursion is shallow at the spec level.
    # But by FEAT-36i, future NKSpec updates may carry parent_ct
    # for the polymorphic id; we assert here on the immediate
    # chain only.
    assert "ipam/ip-addresses/" in call_order


def test_self_referencing_nkspec_does_not_loop() -> None:
    """If devicerole NKSpec ever references devicerole, the
    recursion guard breaks the loop after one pass."""

    from nbsnap.natkey.model import NKField, NKRegistry, NKSpec, Strategy

    reg = NKRegistry()
    reg.register(NKSpec(
        "dcim.devicerole", Strategy.COMPOSITE,
        (NKField("parent", "dcim.devicerole"), NKField("slug")),
    ))

    http = MagicMock()
    calls: list[str] = []
    http.get_all.side_effect = lambda ep: (calls.append(ep), iter([]))[1]

    idx = NKIndex()
    idx.ensure_built(http, reg, "dcim.devicerole")
    # Endpoint visited exactly once even though the NKSpec
    # references its own content type.
    assert len([c for c in calls if "device-roles" in c]) == 1
```

#### Acceptance criteria

* Building an NKIndex for `ipam.ipaddress` from cold cache
  issues no more than `N` paginated GETs where `N` is the
  count of distinct content types in IPAddress's NK dependency
  chain (≤ 3 for the renderer-minimum scope).
* Self-referencing NKSpecs (`dcim.devicerole.parent →
  dcim.devicerole`) do not loop.
* After `ensure_built("ipam.ipaddress")`, a lookup against the
  rescue-10 snapshot's IPAddress NK chain finds the matching
  destination id.

**Estimated Effort:** 1-2h

### [REFINED] [TEST-10] Integration test for demand-driven import order

#### Architectural specification

End-to-end proof that FEAT-36a (snapshot index), FEAT-36b
(look-ahead resolver), FEAT-36c (Phase-2 wiring), and FEAT-23
(Phase-2 writer) compose into a working single-pass import,
even when the snapshot's content-type order is hostile to the
naive sequential importer.

The test runs against the netbox-docker integration stack
already wired up by INFRA-03 (`make stack-up stack-wait`). We
do not need the production NetBox; the test stack is exactly
the controlled environment for this kind of assertion.

Three things the test pins:

1. **Forward references resolve.** A snapshot whose
   `dcim/devices.jsonl` comes before `dcim/sites.jsonl` on
   disk still imports cleanly. The demand-driven resolver
   creates the site on the destination before posting the
   device that references it.
2. **Cycles get deferred and patched.** A device whose
   `primary_ip4` points at its own interface IP (the Device
   ↔ IPAddress ↔ Interface ↔ Device cycle) lands on the
   destination AND has its `primary_ip4` set after Phase-2.
3. **Audit reports the right categories.** No `MISSING_FROM_SOURCE`
   drops; some `OUT_OF_SCOPE` drops (region) are expected and
   acceptable; `DEFERRED_TO_PHASE2` matches the size of the
   deferred queue.

#### REST API details

The test exercises every NetBox endpoint the import path uses:

| Verb | Endpoint | When |
| :--- | :--- | :--- |
| GET  | `/api/status/` | preflight version check |
| GET  | `/api/core/object-types/` or `/api/extras/object-types/` | preflight content-type coverage |
| GET  | `/api/<ct>/?brief=true&limit=500` | NKIndex build (per ct touched) |
| POST | `/api/<ct>/` | record creation (Phase-1 + look-ahead) |
| GET  | `/api/<ct>/<id>/` | upsert skip-if-equal detail fetch |
| PATCH | `/api/<ct>/<id>/` | upsert minimal-diff write, Phase-2 cycle close |

All against `http://localhost:8081` (the netbox-docker dest).
Documentation:
* REST overview: `https://netboxlabs.com/docs/netbox/integrations/rest-api/`
* Per-endpoint shapes: `https://demo.netbox.dev/api/schema/swagger-ui/`

#### Implementation

```python
# tests/integration/test_import_demand_driven.py
"""End-to-end demand-driven import.

Run against the netbox-docker test stacks (require_stack). The
snapshot we feed is intentionally misordered (devices before
sites, cables before interfaces) so the naive sequential
importer would fail; we are proving the look-ahead resolver
compensates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from nbsnap.http.client import NetboxHTTP
from nbsnap.import_.audit import DropCategory
from nbsnap.import_.driver import run_import
from nbsnap.import_.upsert import UpsertOutcome
from nbsnap.schema.openapi import SCHEMA_PATH
from nbsnap.schema.status import VersionSkew

DEST_URL = "http://localhost:8081"
DEST_TOKEN = "abcdef0123456789abcdef0123456789abcdef01"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, sort_keys=True) + "\n")


def _seed_misordered_snapshot(snap: Path) -> None:
    """Build a snapshot directory where devices reference a site
    that the snapshot's OWN jsonl order will not have created
    yet by the time the device is processed. The look-ahead
    resolver should create the site on demand."""

    # The schema can be the test stack's own openapi.json since
    # we are exercising the resolver against a real NetBox.
    schema_resp = requests.get(
        f"{DEST_URL}/api/schema/?format=json",
        headers={"Authorization": f"Token {DEST_TOKEN}"},
        timeout=30,
    )
    schema_resp.raise_for_status()
    (snap / "schema").mkdir(parents=True, exist_ok=True)
    (snap / "schema" / "openapi.json").write_text(
        json.dumps(schema_resp.json()), encoding="utf-8"
    )

    # Manifest with both content types in counts so the topo
    # plan considers them. We omit a deferred_edges list to keep
    # the test focused on demand-driven (not planner) ordering.
    (snap / "manifest.json").write_text(json.dumps({
        "version": 1,
        "source_url": "https://test-source/",
        "netbox_version": "4.6.2",
        "nbsnap_version": "0.0.1",
        "created_at": "2026-06-15T00:00:00+00:00",
        "counts": {"dcim.site": 1, "dcim.device": 1, "dcim.devicerole": 1,
                   "dcim.manufacturer": 1, "dcim.devicetype": 1},
        "perf": {},
        "deferred_edges": [],
    }), encoding="utf-8")

    # Site that the device will refer to.
    _write_jsonl(snap / "dcim/sites.jsonl", [
        {"natural_key": ["test-hall"],
         "body": {"name": "Test Hall", "slug": "test-hall", "status": "active"}},
    ])
    _write_jsonl(snap / "dcim/devicerole.jsonl", [
        {"natural_key": ["test-role"],
         "body": {"name": "Test Role", "slug": "test-role", "color": "808080"}},
    ])
    _write_jsonl(snap / "dcim/manufacturers.jsonl", [
        {"natural_key": ["test-mfr"],
         "body": {"name": "Test Mfr", "slug": "test-mfr"}},
    ])
    _write_jsonl(snap / "dcim/device-types.jsonl", [
        {"natural_key": [["test-mfr"], "test-model"],
         "body": {"manufacturer": ["test-mfr"], "model": "Test Model",
                  "slug": "test-model"}},
    ])
    _write_jsonl(snap / "dcim/devices.jsonl", [
        {"natural_key": [["test-hall"], "test-dev-1"],
         "body": {"name": "test-dev-1",
                  "site": ["test-hall"],
                  "role": ["test-role"],
                  "device_type": [["test-mfr"], "test-model"],
                  "status": "active"}},
    ])


@pytest.mark.usefixtures("require_stack")
def test_demand_driven_imports_misordered_snapshot(tmp_path: Path) -> None:
    """Even when files are visited in alphabetical order, the
    look-ahead resolver creates referenced parents on demand so
    nothing is dropped MISSING_FROM_SOURCE."""

    snap = tmp_path / "snap"
    snap.mkdir()
    _seed_misordered_snapshot(snap)

    http = NetboxHTTP(DEST_URL, DEST_TOKEN, verify_tls=False)
    summary = run_import(
        http, snap, max_skew=VersionSkew.MINOR, on_error="continue",
    )

    # Pin (1): everything in scope landed on the destination.
    assert summary.counts.get(UpsertOutcome.CREATED, 0) >= 5
    assert summary.counts.get(UpsertOutcome.FAILED, 0) == 0, [
        f.message for f in summary.failures
    ]

    # Pin (3): no MISSING_FROM_SOURCE in the audit; OUT_OF_SCOPE
    # is acceptable (region, vrf, etc.), DEFERRED_TO_PHASE2 is fine.
    if getattr(summary, "auditor", None) is not None:
        missing = [
            ev for ev in summary.auditor.events
            if ev.category is DropCategory.MISSING_FROM_SOURCE
        ]
        assert missing == [], [
            f"{ev.child_content_type}.{ev.field_name} -> "
            f"{ev.target_content_type} NK={ev.target_nk}"
            for ev in missing
        ]


@pytest.mark.usefixtures("require_stack")
def test_phase2_closes_primary_ip4_cycle(tmp_path: Path) -> None:
    """Device.primary_ip4 cycle: device lands, IP lands, Phase-2
    PATCHes primary_ip4 onto the device."""

    # Re-use the seeded INFRA-03 fixture data (D39A) by running
    # `make stack-seed` ahead of this test; the docker fixture
    # already has a Device d39a with primary_ip4 set to
    # 172.16.1.10/24 on its Vlan600 interface.

    src = NetboxHTTP("http://localhost:8080",
                     "0123456789abcdef" * 2 + "01234567",
                     verify_tls=False)
    dst = NetboxHTTP(DEST_URL, DEST_TOKEN, verify_tls=False)
    snap = tmp_path / "snap"

    from nbsnap.export.driver import run_export
    run_export(src, snap)
    summary = run_import(dst, snap, max_skew=VersionSkew.MINOR, on_error="continue")

    # Phase-2 should have patched the cycle-closing primary_ip4.
    assert summary.phase2 is not None
    assert summary.phase2.counts.get("patched", 0) >= 1
    assert summary.phase2.is_clean()

    # Cross-verify against the destination GET.
    resp = requests.get(
        f"{DEST_URL}/api/dcim/devices/?name=d39a",
        headers={"Authorization": f"Token {DEST_TOKEN}"},
        timeout=10,
    )
    devices = resp.json()["results"]
    assert devices, "d39a not on destination"
    assert devices[0]["primary_ip4"] is not None
    assert devices[0]["primary_ip4"]["address"] == "172.16.1.10/24"
```

The fixture in `tests/integration/conftest.py` already defines
`require_stack`, so these tests skip cleanly when the
docker stack is down.

#### Acceptance criteria

* `make stack-up stack-wait stack-seed && pytest
  tests/integration/test_import_demand_driven.py -v` runs both
  tests green against a fresh netbox-docker pair.
* On a workstation without the stack, both tests skip with the
  standard "netbox-docker stack is not running" message; the
  rest of the suite is unaffected.
* If FEAT-36b is regressed, the first test fails on the
  `MISSING_FROM_SOURCE` assertion with a list of which FK
  fields fell through.

**Estimated Effort:** 1-2h

---

## Open, Phase 6, Verification

### [FEAT-26a] Snapshot tree diff core (per-file)

**Context:** `goals.md` success criterion 3.

**Requirements:**

- `src/nbsnap/verify/diff.py` with `diff_files(path_a, path_b, *,
  excludes: set[str]) -> list[Delta]`.
- `Delta` dataclass: `kind: Literal["added", "removed", "changed"]`,
  `nk`, `field: str | None`, `before`, `after`.
- For each `.jsonl` file pair, walk records sorted by NK, surface
  per-NK and per-field deltas.

**Testing:** unit test in `tests/unit/test_diff_files.py`. Two files
with one record each, identical, confirm zero deltas. Mutate one
field, confirm one `changed` delta on the right NK and field.
Remove one record, confirm one `removed` delta.

**Estimated Effort:** 1-2h

### [FEAT-26b] Diff CLI sub-command with exclusion list

**Context:** the operator-facing wrapper around `FEAT-26a`.

**Requirements:**

- `src/nbsnap/verify/cli.py` with `run_diff(args) -> int`.
- Flags: `--exclude` (repeatable, default `manifest.exported_at`,
  `performance.*`, NetBox-derived `created`/`last_updated`).
- Walks both snapshot trees, applies `diff_files` per per-type file.
- Prints a unified-diff-like summary to stdout.
- Exits 0 on clean diff, 1 on any delta outside the exclusion set.

**Testing:** integration test in `tests/integration/test_diff_cli.py`.
Generate two snapshots from the same NetBox state, run `nbsnap diff`,
confirm exit 0. Mutate one Device, regenerate, confirm exit 1.

**Estimated Effort:** 1-2h

### [FEAT-27a] Round-trip harness as a Python function

**Context:** the workflow is export A, import B, export B, diff.

**Requirements:**

- `src/nbsnap/verify/roundtrip.py` with `roundtrip(source_http,
  dest_http, work_dir) -> RoundtripResult`.
- Steps: export source to `<work>/a/`, import to dest, export dest
  to `<work>/b/`, diff `a/` against `b/`.
- `RoundtripResult` dataclass: `deltas: list[Delta]`,
  `success: bool`.

**Testing:** unit test in `tests/unit/test_roundtrip_function.py`
with mocked sub-steps. Confirm the orchestration calls each step in
order. Confirm `success` is False if diff produces any delta outside
the exclusion list.

**Estimated Effort:** 1-2h

### [FEAT-27b] Round-trip CLI sub-command exposing the harness

**Context:** operators run the round-trip from the CLI for ad-hoc
audits.

**Requirements:**

- `src/nbsnap/verify/cli.py` extends with `run_roundtrip(args)`.
- Flags: `--source-url`, `--source-token`, `--dest-url`,
  `--dest-token`, `--work-dir`.
- Env precedence from both source and destination.
- **Hard refuse to run against the production source URL per Q23
  burndown.** Before any HTTP call, compare `--source-url` (or its
  env fallback) against `NB_SOURCE_URL` via the `is_source_url`
  helper from `FEAT-01g`. On match, exit non-zero with a message
  pointing at the `CLAUDE.md` production-read-only banner. No
  interactive prompt. The refusal is belt-and-braces over the
  `FEAT-01g` guard rail.
- Refuse to run if the dest is non-empty (count one in-scope
  endpoint, refuse if >0) unless `--allow-non-empty-dest` is set.

**Testing:** integration test in `tests/integration/test_roundtrip_cli.py`
against the two-stack fixture. Confirm exit 0 on a clean run, and a
delta after mutating the source.

**Estimated Effort:** 1-2h

### [TEST-08a1] Renderer-parity topology fixture, Sites through Devices

**Context:** the headline acceptance gate's reference dataset starts
with the static topology (Sites, Locations, Racks, hardware types,
Devices). Q24 burndown selected the hand-built synthetic shape.

**Requirements:**

- Create `tests/fixtures/renderer-parity/01-sites.json` with one
  Site `hall-d` (`name = "Hall D"`).
- Create `tests/fixtures/renderer-parity/02-locations.json` with
  two Locations, `the-forge` (slug `the-forge`, name `The Forge`),
  `mirage-palace` (slug `mirage-palace`, name `Mirage Palace`),
  both in `hall-d`.
- Create `tests/fixtures/renderer-parity/03-racks.json` with four
  Racks, `D39` and `D40` in `the-forge`, `D55` and `D56` in
  `mirage-palace`.
- Create `tests/fixtures/renderer-parity/04-manufacturers.json` with
  `cisco` and `juniper`.
- Create `tests/fixtures/renderer-parity/05-device-types.json` with
  `cisco/ws-c2950t-24` (for access switches) and
  `juniper/ex4100-24t` (for the dist switch).
- Create `tests/fixtures/renderer-parity/06-device-roles.json` with
  `access_switch` and `distribution_switches`.
- Create `tests/fixtures/renderer-parity/07-devices.json` with
  eight access Devices (two per Rack, slot `A` and `B`, names
  `D39A` through `D56B`) plus one dist Device
  `D-THE-FORGE-SW`. All Devices reference their Site, Location,
  Rack, role, device_type from the previous fixture files.

**Testing:** run `python tests/fixtures/seed.py --url <source-url>
--token <source-token> --dir tests/fixtures/renderer-parity/`,
confirm the seeder lands all seven files without errors. Confirm
`GET /api/dcim/devices/?role=access_switch` returns 8 matches and
`?role=distribution_switches` returns 1.

**Estimated Effort:** 1-2h

### [TEST-08a2] Renderer-parity addressing fixture, Interfaces and IPAddresses

**Context:** the second window covers the L2/L3 addressing layer.
Each access switch needs a Vlan600 SVI with a per-switch IPAddress,
the dist switch needs its `ge-0/0/N` ports and the corresponding
`irb.600` SVI.

**Requirements:**

- Create `tests/fixtures/renderer-parity/08-vlans.json` with one
  VLAN `vlan-600` (`vid = 600`, `name = "MGMT"`).
- Create `tests/fixtures/renderer-parity/09-prefixes.json` with one
  Prefix `172.16.1.0/24`, role `kea-dist-mgmt`.
- Create `tests/fixtures/renderer-parity/10-interfaces.json` with,
  one `Vlan600` Interface per access Device (8 total),
  one `Gi0/2` Interface per access Device (uplink),
  eight `ge-0/0/N` Interfaces on the dist Device (N = 0..7),
  one `irb.600` Interface on the dist Device.
- Create `tests/fixtures/renderer-parity/11-ip-addresses.json` with
  eight access IPAddresses (`172.16.1.10/24` through
  `172.16.1.17/24`) assigned to the matching Vlan600 SVIs, plus
  one dist IPAddress (`172.16.1.1/24`) assigned to `irb.600`.

**Testing:** run the seeder, confirm
`GET /api/dcim/interfaces/?device=D39A&name=Vlan600` returns 1
match. Confirm
`GET /api/ipam/ip-addresses/?address=172.16.1.10/24` returns 1
match assigned to `D39A`'s Vlan600 interface. Spot-check the dist
SVI `irb.600` has `172.16.1.1/24`.

**Estimated Effort:** 1-2h

### [TEST-08a3] Renderer-parity cabling fixture and nb2kea verify pass

**Context:** the third window connects the access switches to the
dist switch via Cables and confirms the dataset is renderable
through nb2kea's existing verification script. The verify pass is
the gate that proves the fixture is valid for `TEST-08c`.

**Requirements:**

- Create `tests/fixtures/renderer-parity/12-cables.json` with eight
  Cables. Each connects one access Device's `Gi0/2` to the
  matching dist Device port (`D39A:Gi0/2` to
  `D-THE-FORGE-SW:ge-0/0/0`, then `D39B` to `ge-0/0/1`, and so on
  through `D56B` to `ge-0/0/7`). Use the polymorphic termination
  resolver from the seeder.
- Each dist-side Interface carries an `untagged_vlan` set to
  `vlan-600`, plus a `description` of the form
  `TABLE; D<rack>-<slot>` matching the nb2kea Option 82 convention
  (e.g. `TABLE; D39-A`).
- Run `__reference/nb2kea/scripts/netbox_verify_renderable.py`
  against the seeded source stack as a one-shot validation. Treat
  any error output as a fixture defect, not a bug in the renderer.

**Testing:** run the seeder, confirm
`GET /api/dcim/cables/` returns 8 cables. Then run
`NB_URL=<source-url> NB_TOKEN=<source-token> python
__reference/nb2kea/scripts/netbox_verify_renderable.py`, confirm
exit code 0. Capture the stdout in the test report so a future
fixture drift is visible.

**Estimated Effort:** 1-2h

### [TEST-08b] Run nb2kea renderers against the source as a subprocess

**Context:** the test invokes `__reference/nb2kea/`'s scripts as
subprocesses against the source stack.

**Requirements:**

- `tests/integration/test_renderer_parity_source.py`.
- Wrapper that runs `python __reference/nb2kea/scripts/netbox2cisco.py`,
  `netbox2junos.py`, `netbox2kea.py` against the source stack via
  `NB_URL` and `NB_TOKEN` env vars.
- Capture rendered output into a temp dir.
- Assert each script exits 0 and produces the expected file count
  (one per Device for netbox2cisco / netbox2junos, one global file
  for netbox2kea).

**Testing:** run the test against the seeded source stack, confirm
green. Mutate `__reference/nb2kea/scripts/netbox2cisco.py` to exit 1,
confirm the test fails.

**Estimated Effort:** 1-2h

### [TEST-08c1] Renderer parity roundtrip orchestration

**Context:** the first window of the acceptance gate runs the
roundtrip itself, source export then destination import. The
output of this step is two NetBox stacks holding the same
modelled network. The renderer execution and the diff live in
`TEST-08c2` and `TEST-08c3`.

**Requirements:**

- Create `tests/integration/test_renderer_parity_roundtrip.py`.
- Fixture `seeded_source` that brings the source stack up via
  `make stack-up stack-wait`, then runs the
  `TEST-08a1`/`a2`/`a3` seed fixtures against it.
- Fixture `empty_destination` that brings the destination stack up
  with no seed (the destination starts clean for the cold
  migration).
- Test function `test_roundtrip_lands_clean` that calls the
  `nbsnap.verify.roundtrip` helper from `FEAT-27a` with source
  and destination clients. Assert the call returns
  `RoundtripResult(success=True, deltas=[])`.
- Assert object counts match across the stacks for one canonical
  type (`dcim.device` count from source equals destination).
- Capture the import audit log at
  `<work_dir>/_import.audit.jsonl` and stash the path on the
  pytest record for later windows.

**Testing:** the test function above is the testing step. Run
`pytest tests/integration/test_renderer_parity_roundtrip.py::test_roundtrip_lands_clean -q`,
confirm green. Inspect the audit log, confirm every entry's
result is `CREATED`.

**Estimated Effort:** 1-2h

### [TEST-08c2] Run nb2kea renderers against the destination

**Context:** the second window invokes the three nb2kea renderers
against the destination stack (which holds the imported snapshot
state from `TEST-08c1`). The output lands in a temp directory
the diff step can compare against the source-side output from
`TEST-08b`.

**Requirements:**

- Extend `tests/integration/test_renderer_parity_roundtrip.py`
  with `test_renderers_against_destination`.
- For each of `netbox2cisco.py`, `netbox2junos.py`,
  `netbox2kea.py` under `__reference/nb2kea/scripts/`, run via
  `subprocess.run` with `env={"NB_URL": dest_url, "NB_TOKEN":
  dest_token, ...}` and `cwd=tmp_path`.
- Capture stdout, stderr, and the rendered output files into
  `<tmp_path>/dest-rendered/`.
- Assert every renderer exits 0. The file count matches the
  source-side count from `TEST-08b` (one per Device for
  netbox2cisco / netbox2junos, one global for netbox2kea).
- Reuse the `seeded_source` and `empty_destination` fixtures
  from `TEST-08c1`. The roundtrip from `TEST-08c1` must run
  before this test, declare the dependency with
  `pytest.mark.dependency`.

**Testing:** run the test function, confirm exit 0 for all
renderers and the expected file counts. Pollute the destination
(delete one Device's interfaces) between roundtrip and renderer
run, confirm the renderers either fail or produce divergent
output that `TEST-08c3` catches.

**Estimated Effort:** 1-2h

### [TEST-08c3] Diff the rendered output trees with banner whitelist

**Context:** the third and final window asserts byte equality
between source-side and destination-side renderer output, modulo
the known banner lines that name the source or destination
NetBox hostname (the renderers print `NETBOX_HOST` at the top of
each output).

**Requirements:**

- Extend `tests/integration/test_renderer_parity_roundtrip.py`
  with `test_rendered_outputs_match`.
- Compare `<tmp_path>/source-rendered/` (from `TEST-08b`) against
  `<tmp_path>/dest-rendered/` (from `TEST-08c2`).
- Use `difflib.unified_diff` for each matched filename pair.
- Banner whitelist, lines matching the regex
  `^(\\#|//|!) .* netbox(\.|/)` are normalised through
  `re.sub(r"https?://[^/\\s]+", "https://NETBOX_HOST",
  line)` before the compare.
- Assert the diff list is empty after whitelisting.
- On non-empty diff, attach the diff to the pytest failure
  message so CI shows the divergence.

**Testing:** run end-to-end, confirm green on a clean roundtrip.
Drop the Phase 2 deferred-FK writer call in `nbsnap.import_`,
re-run, confirm the test catches the divergence on
`Device.primary_ip4` paths through the renderer output.

**Estimated Effort:** 1-2h

---

## Open, Phase 7, Operational polish

### [FEAT-28a] Structured logger setup with JSON formatter

**Context:** `PLAN.md` Phase 7. One event per write attempt.

**Requirements:**

- `src/nbsnap/log.py` with `setup_logging(verbose: bool, json_output:
  bool)`.
- Default human formatter on TTY, JSON on non-TTY.
- Levels DEBUG / INFO / WARNING / ERROR / CRITICAL.
- Suppress urllib3 debug output unless `--verbose` is set.

**Testing:** unit test in `tests/unit/test_log_setup.py`. Capture log
output via `caplog`, emit one event at each level, confirm formatter
shape. Confirm `--verbose` flips the root level.

**Estimated Effort:** 1-2h

### [FEAT-28b] Per-write event emission helpers

**Context:** every write site emits a structured event so the audit
log and the operator log agree.

**Requirements:**

- `log.emit_write(phase, content_type, nk, outcome, duration_ms,
  fields_changed=())` helper that emits one JSON event.
- Used by the upsert engine and the Phase-2 writer.

**Testing:** unit test in `tests/unit/test_log_emit_write.py`. Emit
an event, capture JSON, assert keys present.

**Estimated Effort:** 1-2h

### [FEAT-29] TTY progress bars

**Context:** operator UX during long runs.

**Requirements:**

- `src/nbsnap/log.py` adds `progress(label, total)` context manager.
- On TTY, use `rich.progress` (add `rich` to runtime deps).
- On non-TTY, no-op so CI logs stay clean.
- Cancel cleanly on SIGINT, do not leave a broken progress line.

**Testing:** unit test in `tests/unit/test_progress_bar.py`. Force
non-TTY, confirm the context manager is a no-op (no stdout). Force
TTY (use a PTY), confirm output is non-empty.

**Estimated Effort:** 1-2h

### [FEAT-30] End-of-run summary table

**Context:** `docs/05-export-import-workflow.md` Phase I6.

**Requirements:**

- `log.summary_table(rows: list[tuple[str, int, int, int, int]])`.
- Columns: content_type, CREATED, PATCHED, NOOP, FAILED.
- Trailing rows for deferred-FK count and install-local flag count.
- ASCII table rendering (rich.table if rich is already a dep).

**Testing:** unit test in `tests/unit/test_summary_table.py`. Render
a fixture row set, assert the output is non-empty and contains every
column header.

**Estimated Effort:** 1-2h

### [DOC-01a] Operator runbook, cold migration workflow

**Context:** `PLAN.md` Phase 7 exit. Split the runbook by workflow.

**Requirements:**

- `docs/operator-runbook.md` opens with a Safety section per Q25
  burndown. The Safety section carries the production-read-only
  banner verbatim from `CLAUDE.md` and links back as the canonical
  source. The Safety section is at the very top of the runbook
  file, before any workflow heading.
- Add the "Cold migration" workflow heading. Open the section with
  the one-line link "see Safety section above" before any command.
- Steps: prepare destination NetBox (empty), set env vars, run
  preflight, run export, run import, run verify.
- Commands fully spelled out, including the four env vars from
  `CLAUDE.md`.
- Rollback procedure: clear destination via `psql` (acceptable,
  destination is freshly installed and the rollback is a re-deploy).

**Testing:** dry-run the runbook against the test stack. Confirm
each command produces the expected output. Have one teammate read
it and run it without asking questions.

**Estimated Effort:** 1-2h

### [DOC-01b] Operator runbook, parallel deployment workflow

**Context:** the source and destination are sibling NetBoxes serving
different sites.

**Requirements:**

- Extend `docs/operator-runbook.md` with a "Parallel deployment"
  workflow heading. Open with the "see Safety section above" link.
- Step through the install-local flag review (network-only scope,
  so the only category is `IPAddress.dns_name` matching the source
  host per `FEAT-13a`): which entries to keep, which to drop,
  which to rewrite via `--replacement-map`.
- Document the `--allow-source-install-ips` posture and when it is
  acceptable.

**Testing:** dry-run with two test stacks where source IPAMs the
source's own hostname. Verify the operator-facing flag file lists
the finding and the runbook's review step catches it.

**Estimated Effort:** 1-2h

### [DOC-01c] Operator runbook, partial re-sync workflow

**Context:** source has changed, destination needs the delta only.

**Requirements:**

- Extend `docs/operator-runbook.md` with a "Partial re-sync"
  workflow heading. Open with the "see Safety section above" link.
- Step through: incremental export (full re-export, the format is
  cheap to diff), import with `--reject-existing` off so existing
  rows PATCH, verify with `diff`.
- Document the audit log location and how to grep for `PATCHED`
  outcomes to confirm the delta landed.

**Testing:** dry-run with a single mutated Device on the source,
follow the runbook end-to-end, confirm only that one Device is
PATCHED on the destination.

**Estimated Effort:** 1-2h

### [DOC-02a] Performance guide, NetBox-side tuning sections

**Context:** the operator tunes NetBox itself before reaching for
front-proxy or tool-side knobs. This window covers the two
NetBox-side levers, page size and database connection pool.

**Requirements:**

- Create `docs/operator-performance.md` if absent.
- Section "MAX_PAGE_SIZE tuning". Document how NetBox's
  configuration setting interacts with the nbsnap
  `--page-size` flag (`FEAT-17a`). Give a measurement command
  `time nbsnap export --page-size 500` versus
  `--page-size 1000`. Recommend a starting value of 500 with the
  trade-off noted (smaller pages reduce N+1 cost, larger pages
  reduce round-trip count).
- Section "PostgreSQL connection pool sizing". Document the
  `DATABASE` settings block. Give a measurement command using
  `pg_stat_activity` to inspect connection counts during an
  export. Recommend pool size = max-concurrent + 4 headroom.
- Cross-link from `docs/frictions/10`.

**Testing:** dry-run the guide against the test source stack with
the suggested settings, measure two `nbsnap export` runs at
default and tuned values, confirm the timing direction matches
the guide's prediction.

**Estimated Effort:** 1-2h

### [DOC-02b] Performance guide, front-proxy tuning sections

**Context:** when NetBox sits behind nginx or another WAF, the
proxy's rate-limit and body-size caps shape what nbsnap can
push through. This window covers the proxy-side levers.

**Requirements:**

- Extend `docs/operator-performance.md` with a "Front-proxy
  tuning" section.
- Sub-section "nginx rate limits". Include a working
  `limit_req_zone` + `limit_req` excerpt that allows nbsnap's
  retry-friendly burst pattern (500 reqs per 30 seconds, burst
  100). Reference RFC 9110 `Retry-After` semantics already
  honoured by `FEAT-01d`.
- Sub-section "Request and response body size caps". Recommend
  `client_max_body_size 32m` (covers the OpenAPI schema fetch
  which can run to several MB). Recommend
  `proxy_read_timeout 60s` for slower NetBox responses.
- Sub-section "Concurrency limits". Document
  `limit_conn_zone` + `limit_conn` and the interaction with
  `--max-concurrent`.
- Each sub-section names a curl or `nginx -T` command for the
  operator to inspect the live config.

**Testing:** apply one nginx rate-limit excerpt to a test proxy
in front of the source stack, confirm a `nbsnap export` retry
schedule survives the limit. Confirm `Retry-After` lands and
the export completes.

**Estimated Effort:** 1-2h

### [DOC-02c] Performance guide, GraphQL and bulk endpoint decision criteria

**Context:** the tool can opt into GraphQL (`RES-06`) and bulk
endpoints (`RES-07`) for specific read and write paths. The
guide names the trigger conditions so the operator knows when
to flip the flag.

**Requirements:**

- Extend `docs/operator-performance.md` with a "When to use
  GraphQL" section. Document the >30 percent wall-time gain
  threshold from `RES-06`. Cross-link to
  `docs/implementation/08-graphql-benchmark.md` for the
  measurement methodology. Name the two endpoints the gain
  is expected on (`dcim/devices/`, `ipam/ip-addresses/`).
- Extend with a "When to use bulk endpoints" section. Document
  the per-record error-handling cost vs throughput trade-off
  from `RES-07`. Name the two opt-in candidates,
  `--bulk-endpoints cables,interfaces`. Recommend the
  measurement, `time nbsnap import` with and without the
  flag on the `TEST-09a` 50k fixture.
- Add a "Decision flow" diagram (ASCII art) showing the order
  operators should evaluate, NetBox-side first, proxy second,
  GraphQL third, bulk fourth.

**Testing:** self-review confirms the decision flow matches the
RES-06 / RES-07 decision rules. Run a benchmark for the
GraphQL and bulk recommendations against the test stack,
confirm the numbers in the guide reflect a real measurement
not a guess.

**Estimated Effort:** 1-2h

### [DOC-03] Implementation notes index

**Context:** `docs/implementation/` carries per-decision rationales.
The index makes them findable.

**Requirements:**

- Create `docs/implementation/00-INDEX.md`.
- One line per implementation note linking to its file with a
  one-sentence summary.
- Cross-link from `docs/INDEX.md`.

**Testing:** click every link in the index, confirm targets exist.
Confirm every `docs/implementation/*.md` file is in the index.

**Estimated Effort:** 1-2h

### [FEAT-34] nbsnap pack, snapshot directory to .nbsnap.tar.zst

**Context:** per Q4 burndown, ship pack and unpack in v1 so
operators can share snapshots as one artefact. Backs the `pack`
stub from `INFRA-02b`.

**Requirements:**

- `src/nbsnap/pack/cli.py` with `run_pack(args) -> int`.
- Flags, `--in <snapshot-dir>`, `--out <pack-file>`,
  `--compression {zstd, gz, none}` (default `zstd`).
- Compose a deterministic tar (sorted entries, fixed mtimes) so
  the same input produces a byte-identical pack on two runs.
- Compute SHA256 over the canonicalised tar bytes, embed the hash
  in the manifest's `pack_sha256` field and also write a sidecar
  `<pack-file>.sha256`. The hash gates `FEAT-35` integrity check.
- Use `tarfile` plus the `zstandard` library for zstd (add to
  `[project.dependencies]`).
- Replace the stub in `cli.py`.
- Document the format in `docs/04-snapshot-format.md` "Packed
  form" section.

**Testing:** unit test in `tests/unit/test_pack.py`. Build a small
snapshot dir, pack twice, confirm byte-identical output. Confirm
SHA matches the sidecar. Integration test packs the seeded export
output and round-trips through `FEAT-35` unpack.

**Estimated Effort:** 1-2h

### [FEAT-35] nbsnap unpack, .nbsnap.tar.zst to snapshot directory

**Context:** counterpart to `FEAT-34`. Validates the SHA before
extracting. Backs the `unpack` stub from `INFRA-02b`.

**Requirements:**

- `src/nbsnap/pack/cli.py` extends with `run_unpack(args) -> int`.
- Flags, `--in <pack-file>`, `--out <snapshot-dir>`,
  `--skip-sha-check` (off by default, on for forensic recovery).
- Read the sidecar SHA file, compute the SHA over the tar bytes,
  refuse on mismatch unless `--skip-sha-check` is passed.
- Refuse on existing non-empty `--out` to avoid silent merge,
  override with `--force`.
- Replace the stub in `cli.py`.

**Testing:** unit test in `tests/unit/test_unpack.py`. Round-trip
pack then unpack, confirm directory tree identical. Mutate the
pack bytes by one byte, confirm unpack refuses on SHA mismatch.
Confirm `--skip-sha-check` lets it through with a clear warning.

**Estimated Effort:** 1-2h

### [FEAT-37] `nbsnap reset-destination`, wipe in-scope objects from the destination NetBox

**Status:** open (unchecked).

**Context:** during testing, rescue iteration, and the
"oops, the import wrote the wrong thing" scenario, an operator
needs a way to bring the destination NetBox back to a clean
slate so the next `nbsnap import` can run from scratch. Today
the only options are (a) drop the destination's PostgreSQL
database (heavy hammer, requires shell access), or (b) click
through the NetBox UI per content type (slow, error-prone).

This ticket introduces a new CLI sub-command,
`nbsnap reset-destination`, that walks every in-scope content
type in **reverse topological order** and DELETEs each record
via the destination NetBox's REST API. Reverse-topo because
children must die before their parents to avoid Django's
`PROTECT` foreign-key constraint.

**Critical safety constraints, locked in at design time:**

1. The command **refuses to run against `NB_SOURCE_URL`**. Both
   guard-rail layers (constructor + per-request) already in
   `NetboxHTTP` apply, plus an explicit double-check in the
   subcommand that calls `client.is_source()` and exits 4 with
   "destination URL matches NB_SOURCE_URL, refusing to delete"
   before any GET.
2. **Dry-run is the default.** Without `--apply`, the command
   enumerates what it WOULD delete and prints a per-content-type
   count, but issues zero DELETE calls.
3. **Explicit destructive flag required.** Even with `--apply`,
   the command refuses unless `--i-know-what-im-doing` is also
   passed. No interactive prompts so the command is scriptable
   in CI, but both flags must appear in the invocation.
4. **Scope is opt-in.** Default scope is the renderer-minimum
   set (`nbsnap.export.driver.DEFAULT_SCOPE`). `--content-types`
   narrows or widens. The command never touches
   `users.*`, `extras.objectpermission`, `extras.token`,
   `core.*`, or any plugin content type unless explicitly named.
5. **`--keep` exclusions.** `--keep <slug-or-name>` repeatable,
   lets the operator pin specific records that survive the wipe
   (e.g. `--keep "admin@example.invalid"`-tagged tags, the
   bootstrap "default-site").

#### Architectural specification

Three phases per content type, all driven by the same NetBox
REST API patterns nbsnap already uses elsewhere:

1. **Enumerate.** `GET /api/<endpoint>/?limit=500` paginated
   via the `next` link, collect `id` for every row. Apply the
   `--keep` filter at this stage so kept rows never enter the
   delete queue.
2. **Confirm.** When dry-run, print
   `<content_type>: would delete <n> records`.
   When `--apply --i-know-what-im-doing`, print
   `<content_type>: deleting <n> records...` and proceed.
3. **Delete.** Issue `DELETE /api/<endpoint>/` with a body
   `[{"id": 1}, {"id": 2}, ...]`, batched at 100 ids per call
   to stay under typical front-proxy body-size limits.

The reverse-topo order comes from running the existing planner
(`nbsnap.graph.plan`) over the same OpenAPI schema, then
iterating the plan in reverse. This guarantees Cables die
before Interfaces before Devices before Locations before
Sites, etc. Any record whose DELETE 409s because something
out-of-scope still references it gets logged as a failure
(`--on-error continue` carries on; `--on-error stop` aborts).

#### REST API details

All endpoints are read-write on the destination NetBox. Source
guard refuses if the base URL matches `NB_SOURCE_URL`.

* **Enumerate** (paged GET):

      GET /api/<endpoint>/?limit=500
      Authorization: Token <NB_DESTINATION_TOKEN>
      Accept: application/json

  Response:

      200 OK
      {
        "count": 130,
        "next": "https://.../?limit=500&offset=500",
        "previous": null,
        "results": [
          {"id": 1, "url": "...", "display": "...", "name": "..."},
          ...
        ]
      }

  Reference: `https://netboxlabs.com/docs/netbox/integrations/rest-api/`
  pagination section. The endpoint table reuses
  `nbsnap.natkey.verify.CONTENT_TYPE_ENDPOINTS`.

* **Bulk delete** (NetBox 4.x supports an array body on the
  list endpoint, documented at the Swagger UI under each
  resource's `bulk_destroy`):

      DELETE /api/<endpoint>/
      Authorization: Token <NB_DESTINATION_TOKEN>
      Content-Type: application/json

      [{"id": 1}, {"id": 2}, {"id": 3}]

  Response on success:

      204 No Content

  Response on FK constraint violation:

      409 Conflict
      {"detail": "Unable to delete object. <Other> objects depend on it."}

  Reference: `https://demo.netbox.dev/api/schema/swagger-ui/`,
  search each content type's `_bulk_destroy` operation.

* **Single delete fallback**, in case bulk fails for one bad
  row in the batch:

      DELETE /api/<endpoint>/<id>/
      Authorization: Token <NB_DESTINATION_TOKEN>

  Response: `204 No Content` on success, `409 Conflict` if the
  record still has dependents.

The implementation should attempt bulk first per batch, and on
a 4xx response that names a single offending id, fall back to
single-row DELETE for the rest of the batch so one bad row
does not stall the whole content type.

#### Implementation

```python
# src/nbsnap/reset_cli.py
"""nbsnap reset-destination, wipe in-scope objects from the dest.

Three safety layers:
  1. Source-URL guard rail (NetboxHTTP already enforces).
  2. --apply must be passed explicitly; dry-run is the default.
  3. --i-know-what-im-doing must ALSO be passed before any DELETE.

Reverse-topological delete order so Django PROTECT FKs do not
trip on a parent that still has children.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from nbsnap.export.driver import DEFAULT_SCOPE
from nbsnap.graph import from_openapi, plan as build_plan
from nbsnap.http.client import NetboxHTTP, NetboxHTTPError
from nbsnap.natkey.verify import CONTENT_TYPE_ENDPOINTS
from nbsnap.schema.openapi import OpenAPI

logger = logging.getLogger(__name__)
EXIT_OK = 0
EXIT_BLOCKED_BY_SOURCE_GUARD = 4
EXIT_NEEDS_APPLY_FLAGS = 1
EXIT_DELETE_FAILURES = 2
BATCH = 100


def add_reset_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", help="NetBox base URL; defaults to NB_DESTINATION_URL")
    parser.add_argument("--token", help="NetBox API token; defaults to NB_DESTINATION_TOKEN")
    parser.add_argument("--no-verify-tls", action="store_true",
                        help="disable TLS verification")
    parser.add_argument("--content-types",
                        help="comma-separated content types; default is the renderer-minimum scope")
    parser.add_argument("--keep", action="append", default=[],
                        metavar="<name-or-slug>",
                        help="exclude records whose name/slug matches; repeatable")
    parser.add_argument("--apply", action="store_true",
                        help="actually issue DELETE calls; default is dry-run")
    parser.add_argument("--i-know-what-im-doing", action="store_true", dest="confirmed",
                        help="required alongside --apply, no interactive prompt")
    parser.add_argument("--on-error", choices=["stop", "continue"], default="stop")
    parser.add_argument("--audit-out", type=Path, default=None,
                        help="write a per-record JSONL audit of deletes (default stderr only)")


def run_reset_cli(args: argparse.Namespace) -> int:
    """Entry point. Builds the client, computes the reverse plan,
    walks each content type, and DELETEs."""

    # Construct the client. The standard from_env() helper does
    # the role-specific env lookup AND attaches the source guard.
    http = NetboxHTTP.from_env(
        "destination",
        url=args.url, token=args.token, verify_tls=not args.no_verify_tls,
    )

    # Safety layer 1: triple-check the source guard at the CLI
    # boundary, in case the operator passed --url that happens
    # to match NB_SOURCE_URL.
    if http.is_source():
        sys.stderr.write(
            "nbsnap reset-destination: refusing, destination URL matches "
            f"NB_SOURCE_URL ({http.base_url}). The source NetBox is read-only "
            "by policy (see CLAUDE.md).\n"
        )
        return EXIT_BLOCKED_BY_SOURCE_GUARD

    # Safety layer 2 + 3: dry-run vs explicit destructive flag.
    if args.apply and not args.confirmed:
        sys.stderr.write(
            "nbsnap reset-destination: --apply also requires "
            "--i-know-what-im-doing.\n"
            "This command will DELETE every in-scope object on:\n"
            f"  {http.base_url}\n"
            "Re-run with both flags to proceed.\n"
        )
        return EXIT_NEEDS_APPLY_FLAGS

    # Resolve scope.
    scope = (
        set(DEFAULT_SCOPE)
        if not args.content_types
        else {t.strip() for t in args.content_types.split(",") if t.strip()}
    )
    keep = set(args.keep)

    # Build reverse-topo plan from the destination's live schema.
    # We could load a snapshot's schema too, but the destination is
    # the authoritative source of truth for which records exist.
    openapi = OpenAPI.fetch(http)
    graph = from_openapi(openapi, scope=scope)
    plan_obj = build_plan(graph)
    delete_order = list(reversed(plan_obj.order))

    audit_lines: list[str] = []
    counts: Counter[str] = Counter()
    failures: list[tuple[str, int, str]] = []

    for ct in delete_order:
        if ct not in scope:
            continue
        endpoint = CONTENT_TYPE_ENDPOINTS.get(ct)
        if endpoint is None:
            continue

        ids = list(_enumerate_ids(http, endpoint, keep_names=keep))
        if not ids:
            counts[ct] = 0
            continue

        verb = "DELETING" if args.apply and args.confirmed else "would delete"
        sys.stderr.write(f"  {ct}: {verb} {len(ids)} records\n")

        if not (args.apply and args.confirmed):
            counts[ct] = len(ids)
            continue

        for batch in _chunks(ids, BATCH):
            try:
                _bulk_delete(http, endpoint, batch)
                counts[ct] += len(batch)
                for rid in batch:
                    audit_lines.append(json.dumps(
                        {"content_type": ct, "id": rid, "outcome": "deleted"}
                    ))
            except NetboxHTTPError as exc:
                # Fall back to per-id deletes so one bad row
                # does not stall the rest of the batch.
                for rid in batch:
                    try:
                        http._request("DELETE", f"{endpoint}{rid}/")
                        counts[ct] += 1
                        audit_lines.append(json.dumps(
                            {"content_type": ct, "id": rid, "outcome": "deleted-fallback"}
                        ))
                    except NetboxHTTPError as one_exc:
                        failures.append((ct, rid, str(one_exc)))
                        audit_lines.append(json.dumps(
                            {"content_type": ct, "id": rid, "outcome": "failed",
                             "message": str(one_exc)[:200]}
                        ))
                        if args.on_error == "stop":
                            _flush_audit(args, audit_lines)
                            _summary(counts, failures, dry_run=False)
                            return EXIT_DELETE_FAILURES

    _flush_audit(args, audit_lines)
    _summary(counts, failures, dry_run=not (args.apply and args.confirmed))
    return EXIT_DELETE_FAILURES if failures else EXIT_OK


# Helpers --------------------------------------------------------

def _enumerate_ids(
    http: NetboxHTTP, endpoint: str, keep_names: set[str]
) -> Iterable[int]:
    """Yield every id NetBox lists for `endpoint`, minus --keep matches."""

    for row in http.get_all(endpoint):
        rid = row.get("id")
        if not isinstance(rid, int):
            continue
        name = row.get("name") or row.get("slug") or ""
        if name in keep_names:
            continue
        yield rid


def _chunks(items: list[int], n: int) -> Iterable[list[int]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _bulk_delete(http: NetboxHTTP, endpoint: str, ids: list[int]) -> None:
    """NetBox bulk DELETE expects `[{"id": <int>}, ...]` body."""

    body = [{"id": rid} for rid in ids]
    http._request("DELETE", endpoint, json=body)


def _flush_audit(args: argparse.Namespace, lines: list[str]) -> None:
    if args.audit_out is None:
        return
    args.audit_out.parent.mkdir(parents=True, exist_ok=True)
    args.audit_out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summary(
    counts: Counter[str], failures: list[tuple[str, int, str]], *, dry_run: bool
) -> None:
    sys.stderr.write("# nbsnap reset-destination "
                     + ("(dry-run)" if dry_run else "complete") + "\n")
    for ct, n in sorted(counts.items()):
        sys.stderr.write(f"  {ct}: {n}\n")
    if failures:
        sys.stderr.write(f"  failed: {len(failures)}\n")
        sys.stderr.write(f"  first failure: {failures[0][0]} id={failures[0][1]} "
                         f"{failures[0][2][:160]}\n")
```

Wire into `src/nbsnap/cli.py` alongside the other sub-commands.
Append `"reset-destination": "FEAT-37"` to the `TICKETS` map and
add the dispatcher branch:

```python
elif name == "reset-destination":
    from nbsnap.reset_cli import add_reset_args, run_reset_cli
    add_reset_args(sub)
    sub.set_defaults(func=run_reset_cli)
```

#### Tests

```python
# tests/unit/test_reset_cli.py
"""Test the safety layers, dry-run accounting, and bulk-DELETE wiring."""

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nbsnap.reset_cli import (
    EXIT_BLOCKED_BY_SOURCE_GUARD,
    EXIT_NEEDS_APPLY_FLAGS,
    EXIT_OK,
    run_reset_cli,
)


def _args(**over) -> argparse.Namespace:
    defaults = {
        "url": "https://dest.example/",
        "token": "tok",
        "no_verify_tls": False,
        "content_types": None,
        "keep": [],
        "apply": False,
        "confirmed": False,
        "on_error": "stop",
        "audit_out": None,
    }
    defaults.update(over)
    return argparse.Namespace(**defaults)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for k in ("NB_SOURCE_URL", "NB_SOURCE_TOKEN",
              "NB_DESTINATION_URL", "NB_DESTINATION_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def test_refuses_to_run_against_source_url(monkeypatch):
    """The source-URL guard fires at the CLI boundary."""
    monkeypatch.setenv("NB_SOURCE_URL", "https://prod.example/")
    rc = run_reset_cli(_args(url="https://prod.example/"))
    assert rc == EXIT_BLOCKED_BY_SOURCE_GUARD


def test_refuses_apply_without_confirmation():
    """--apply alone is not enough."""
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env",
               return_value=MagicMock(is_source=lambda: False)):
        rc = run_reset_cli(_args(apply=True, confirmed=False))
    assert rc == EXIT_NEEDS_APPLY_FLAGS


def test_dry_run_does_not_call_delete():
    """Default mode enumerates but does not delete."""

    http = MagicMock()
    http.is_source.return_value = False
    http.base_url = "https://dest.example/"
    http.get_all.return_value = iter([
        {"id": 1, "name": "A"}, {"id": 2, "name": "B"},
    ])
    http._request.return_value = None

    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=http), \
         patch("nbsnap.reset_cli.OpenAPI.fetch", return_value=MagicMock()), \
         patch("nbsnap.reset_cli.from_openapi") as fop, \
         patch("nbsnap.reset_cli.build_plan") as bp:
        bp.return_value = MagicMock(order=["dcim.site"])
        fop.return_value = MagicMock()
        rc = run_reset_cli(_args(apply=False, confirmed=False))

    assert rc == EXIT_OK
    # No DELETE was issued.
    delete_calls = [c for c in http._request.mock_calls
                    if c.args and c.args[0] == "DELETE"]
    assert delete_calls == []


def test_apply_with_confirmation_issues_bulk_delete():
    """Both safety flags set, bulk DELETE fires with a list body."""

    http = MagicMock()
    http.is_source.return_value = False
    http.base_url = "https://dest.example/"
    http.get_all.return_value = iter([
        {"id": 1, "name": "A"}, {"id": 2, "name": "B"},
    ])
    http._request.return_value = None  # 204

    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=http), \
         patch("nbsnap.reset_cli.OpenAPI.fetch", return_value=MagicMock()), \
         patch("nbsnap.reset_cli.from_openapi") as fop, \
         patch("nbsnap.reset_cli.build_plan") as bp:
        bp.return_value = MagicMock(order=["dcim.site"])
        fop.return_value = MagicMock()
        rc = run_reset_cli(_args(apply=True, confirmed=True))

    assert rc == EXIT_OK
    delete_calls = [c for c in http._request.mock_calls
                    if c.args and c.args[0] == "DELETE"]
    assert len(delete_calls) == 1
    _, kwargs = delete_calls[0].kwargs, delete_calls[0]
    assert delete_calls[0].kwargs["json"] == [{"id": 1}, {"id": 2}]


def test_keep_excludes_named_records():
    """--keep <name> drops the matching row from the delete list."""

    http = MagicMock()
    http.is_source.return_value = False
    http.base_url = "https://dest.example/"
    http.get_all.return_value = iter([
        {"id": 1, "name": "keep-me"},
        {"id": 2, "name": "B"},
    ])

    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=http), \
         patch("nbsnap.reset_cli.OpenAPI.fetch", return_value=MagicMock()), \
         patch("nbsnap.reset_cli.from_openapi") as fop, \
         patch("nbsnap.reset_cli.build_plan") as bp:
        bp.return_value = MagicMock(order=["dcim.site"])
        fop.return_value = MagicMock()
        run_reset_cli(_args(apply=True, confirmed=True, keep=["keep-me"]))

    delete_calls = [c for c in http._request.mock_calls
                    if c.args and c.args[0] == "DELETE"]
    assert delete_calls[0].kwargs["json"] == [{"id": 2}]


def test_audit_jsonl_written_when_path_given(tmp_path: Path):
    """--audit-out persists per-record outcomes for forensics."""

    http = MagicMock()
    http.is_source.return_value = False
    http.base_url = "https://dest.example/"
    http.get_all.return_value = iter([{"id": 7, "name": "X"}])
    http._request.return_value = None

    audit_path = tmp_path / "audit.jsonl"
    with patch("nbsnap.reset_cli.NetboxHTTP.from_env", return_value=http), \
         patch("nbsnap.reset_cli.OpenAPI.fetch", return_value=MagicMock()), \
         patch("nbsnap.reset_cli.from_openapi") as fop, \
         patch("nbsnap.reset_cli.build_plan") as bp:
        bp.return_value = MagicMock(order=["dcim.site"])
        fop.return_value = MagicMock()
        run_reset_cli(_args(apply=True, confirmed=True, audit_out=audit_path))

    rows = [json.loads(line) for line in audit_path.read_text().splitlines() if line]
    assert {"content_type": "dcim.site", "id": 7, "outcome": "deleted"} in rows
```

#### Integration test, against the docker stack

```python
# tests/integration/test_reset_destination.py
"""End-to-end against the dest stack: seed it, reset, confirm empty."""

import pytest
import requests

from nbsnap.http.client import NetboxHTTP
from nbsnap.reset_cli import run_reset_cli

DEST_URL = "http://localhost:8081"
DEST_TOKEN = "abcdef0123456789" * 2 + "abcdef01"


def _args(**over):
    import argparse
    a = argparse.Namespace(
        url=DEST_URL, token=DEST_TOKEN, no_verify_tls=True,
        content_types=None, keep=[], apply=True, confirmed=True,
        on_error="continue", audit_out=None,
    )
    for k, v in over.items():
        setattr(a, k, v)
    return a


@pytest.mark.usefixtures("require_stack")
def test_reset_clears_seeded_destination():
    """After `make stack-seed`, destination has sites and devices.
    After reset, those endpoints return zero."""

    # Sanity check, the seed put data in.
    headers = {"Authorization": f"Token {DEST_TOKEN}"}
    pre = requests.get(f"{DEST_URL}/api/dcim/sites/",
                       headers=headers, timeout=10).json()["count"]
    assert pre > 0, "test stack not seeded, run `make stack-seed` first"

    rc = run_reset_cli(_args())
    assert rc == 0

    post = requests.get(f"{DEST_URL}/api/dcim/sites/",
                        headers=headers, timeout=10).json()["count"]
    assert post == 0
```

#### Acceptance criteria

1. `nbsnap reset-destination --url $NB_SOURCE_URL ...` returns
   exit 4 with the source-guard message, never touches the
   wire.
2. `nbsnap reset-destination --url $NB_DESTINATION_URL --token
   $NB_DESTINATION_TOKEN` (no `--apply`) prints a per-
   content-type "would delete N records" table and returns
   exit 0, zero DELETE calls.
3. `nbsnap reset-destination --apply` without
   `--i-know-what-im-doing` returns exit 1 with a clear
   message, zero DELETE calls.
4. `nbsnap reset-destination --apply --i-know-what-im-doing`
   walks reverse-topological order, DELETEs every in-scope
   record, and exits 0 if no 409s land.
5. After running against a seeded netbox-docker dest stack,
   `GET /api/dcim/sites/` returns `count: 0` and the same for
   every other in-scope endpoint.
6. `--keep "my-tag"` survives the wipe, end-to-end verified.
7. An out-of-scope FK pointing into the in-scope record set
   produces a 409 on the in-scope DELETE; the failure lands in
   the audit JSONL, and the run continues (`--on-error
   continue`) or stops (`--on-error stop`) as configured.

**Estimated Effort:** 2-3h

---

## Open, Phase 8, Extensions

### [FEAT-31a] Plugin discovery via entry-points

**Context:** `docs/frictions/09` M2.

**Requirements:**

- `src/nbsnap/plugins/registry.py` with `class PluginRegistry`.
- `PluginRegistry.discover()` enumerates `nbsnap.plugin` entry-points
  via `importlib.metadata.entry_points`.
- Each entry-point's callable returns a `PluginExtension` instance.
- The registry catches per-plugin errors (import, init) and emits a
  WARNING per failed plugin, continues with the others.
- **Network-only scope warning per Q26 burndown.** For each
  registered content type that is not in the in-scope set from
  `FEAT-08b` `DEFAULT_SPECS`, emit one WARNING line per
  registration with the format "plugin `<name>` registers
  out-of-scope content type `<content_type>`, snapshot will not
  carry its values". No hard refusal, the scope filter still drops
  the values during export, the warning gives the plugin author a
  clear signal.

**Testing:** unit test in `tests/unit/test_plugin_discovery.py`.
Register a fake entry-point pointing at a no-op callable, confirm
discovery picks it up. Register a broken one, confirm discovery
emits a warning but does not crash.

**Estimated Effort:** 1-2h

### [FEAT-31b] PluginExtension protocol and registration shape

**Context:** the contract plugin authors implement.

**Requirements:**

- `src/nbsnap/plugins/protocol.py` with `class PluginExtension(Protocol)`.
- Methods: `name() -> str`, `min_plugin_version() -> str`,
  `object_types() -> list[ObjectTypeSpec]`, where `ObjectTypeSpec`
  carries `content_type`, `endpoint`, `nk_spec`, `scope_layer`,
  `field_allowlist`.
- `PluginRegistry.merge_into(default_specs, default_scope) ->
  tuple[dict, set]` extends the spec table and the in-scope set
  with discovered extensions.
- **Document the network-only scope rule in
  `docs/implementation/06-plugin-protocol.md` per Q26 burndown.**
  The contract states, extensions may only register content types
  that fit the network model (DCIM, IPAM, network-adjacent
  Tenancy). Extensions are free to register other types but the
  runtime emits a WARNING per `FEAT-31a` and the scope filter
  drops the values during export. The doc names
  `netbox-bgp`/`netbox-dns` as canonical in-scope plugins,
  `netbox-secrets` as a canonical out-of-scope plugin.

**Testing:** unit test in `tests/unit/test_plugin_protocol.py`. Build
a fake `PluginExtension` returning one ObjectTypeSpec. Merge into
the default specs, confirm the new content type is present.

**Estimated Effort:** 1-2h

### [FEAT-32] Sketch reference extension for netbox-bgp

**Context:** `docs/frictions/09` M7. Worked example without leaving
this repo.

**Requirements:**

- `docs/implementation/07-plugin-netbox-bgp.md` with a complete
  sketch: pyproject layout, the `register()` callable, the
  ObjectTypeSpec entries for `netbox_bgp.bgpsession`,
  `netbox_bgp.bgppeergroup`.
- Identify natural keys for each (likely `(device.{key}, remote_as,
  remote_ip)` for sessions, `slug` for peer groups).
- Do not ship the extension in this repo. The doc is the
  deliverable, the sibling repo lands separately.

**Testing:** review the doc with one engineer familiar with
`netbox-bgp`. Confirm the proposed NKs are unique against a real
netbox-bgp dataset (manual check).

**Estimated Effort:** 1-2h

### [RES-06] Decide GraphQL adoption schedule

**Context:** `docs/frictions/10` M9. Benchmark gates the choice.

**Requirements:**

- Author `docs/implementation/08-graphql-benchmark.md`.
- Define benchmark methodology: two endpoints
  (`dcim/devices/`, `ipam/ip-addresses/`), measure REST and GraphQL
  wall time over the seeded source.
- Adopt only if GraphQL is >30% faster on both.
- Record the measurement in the doc with the test invocation.

**Testing:** run the benchmark on a healthy test stack. Capture
numbers in the doc. Re-run to confirm reproducibility within 10%.

**Estimated Effort:** 1-2h

### [RES-07] Decide bulk endpoint adoption schedule

**Context:** `docs/frictions/10` M6.

**Requirements:**

- Author `docs/implementation/09-bulk-endpoints.md`.
- Default off, opt-in via `--bulk-endpoints cables,interfaces`.
- Document the per-record error-handling cost of bulk POST.
- Cross-link from `docs/operator-performance.md`.

**Testing:** self-review confirms the trade-off is named and the
opt-in is documented. Confirm the opt-in flag is present in the CLI
parser for `nbsnap import` (or scheduled to land in a later
ticket).

**Estimated Effort:** 1-2h

### [RES-08] Decide v1.1 source for the renderer-parity dataset

**Context:** `TEST-08a` ships a hand-built synthetic fixture for
v1 per Q24 burndown. v1.1 should pick a richer dataset to catch
behaviours the synthetic fixture misses. Decision recorded here so
v1 ships without committing to the long-term source.

**Requirements:**

- Author `docs/implementation/10-renderer-parity-v1.1-source.md`.
- Compare two options:
  - Sanitised export from production. Real shape, operator effort
    to scrub hostnames and IPs. Closest to "what production
    actually carries".
  - Generated by running `__reference/nb2kea/` bootstrap scripts
    against a fresh test stack. Self-documenting, uses the actual
    bootstrap path, couples the test to those script APIs.
- Pick one with explicit rationale and a "what would force a flip"
  line.
- Define the v1.1 test plan, including how the chosen dataset
  integrates with `TEST-08c`.
- Decide before Phase 8 enrichment lands so the scale test in
  `TEST-09` does not duplicate the work.

**Testing:** self-review confirms both options are weighed against
production fidelity, maintenance cost, and operator effort. One
teammate sign-off in the PR thread before merge.

**Estimated Effort:** 1-2h

---

## Open, Phase 9, Hardening and release

### [FEAT-33a1] Security review checklist body, four standard sections

**Context:** the first half of the security review document covers
the four standard concerns, token handling, TLS posture,
install-local classification, supply chain. Each section ends with
a self-test command an operator or CI gate can run.

**Requirements:**

- Create `docs/security-review.md`.
- Section "Token handling". Self-test command,
  `grep -rE "NB_(SOURCE|DESTINATION)?_TOKEN" src/ tests/` should
  only match `os.environ.get` reads and the test fixtures. No
  hardcoded values, no log calls referencing the token variable.
- Section "TLS posture". Self-test command, run
  `nbsnap export --url https://example.invalid --token x`,
  confirm the call refuses with a TLS verification error unless
  `--no-verify-tls` is explicitly passed.
- Section "Install-local classification correctness". Self-test
  command, run the export against the seeded source stack, scan
  `_flagged/install-local.jsonl` for the expected
  `MATCHES_SOURCE_NETBOX_HOST` finding on
  `netbox.infra.glitched.se`, fail if absent.
- Section "Supply chain". Self-test command, `pip install
  --dry-run -e .` lists no unpinned transitive dependencies.
  `sigstore verify` against the release artefact (per `REL-01b`)
  confirms the signature.

**Testing:** self-review confirms each section names a concrete
self-test command. Run each command against the current state,
confirm green. Cross-link from `PLAN.md` Phase 9 scope.

**Estimated Effort:** 1-2h

### [FEAT-33a2] Source read-only invariant section with static self-tests

**Context:** the second half of the security review document is
the most safety-critical, the source read-only invariant section.
Per Q27 burndown, the static self-test combines a grep command
with a custom ruff rule. The runtime self-test lives in
`FEAT-33a3`.

**Requirements:**

- Extend `docs/security-review.md` with a "Source read-only
  invariant" section.
- Static self-test 1, `grep -rE
  "client\.(post|patch|put|delete)" src/ | grep -i source`
  returns empty. The command is documented in the section with
  the expected output.
- Static self-test 2, custom ruff rule `nbsnap-no-source-writes`
  written as a Python plugin under
  `tooling/ruff_plugins/no_source_writes.py`. The rule walks the
  AST, detects any `NetboxHTTP.<verb>` call where the receiver's
  constructor argument literal-matches `NB_SOURCE_URL` or
  `os.environ["NB_SOURCE_URL"]`, fires a `NBS001` lint error.
- Wire the custom rule via `[tool.ruff.lint.extend-select]` in
  `pyproject.toml`.
- Section documents the trigger pattern, the suppression syntax
  (none, the rule cannot be silenced), and the failure mode if
  the rule misses a dynamic verb (the runtime self-test from
  `FEAT-33a3` catches dynamic verbs).

**Testing:** unit test in
`tests/unit/test_ruff_plugin_no_source_writes.py`. Feed the rule
a synthetic AST representing `NetboxHTTP(NB_SOURCE_URL).post(...)`,
confirm `NBS001` fires. Feed `NetboxHTTP(NB_DESTINATION_URL).post(...)`,
confirm clean. Run `ruff check src/` on the current code base,
confirm zero `NBS001` violations.

**Estimated Effort:** 1-2h

### [FEAT-33b] Wire the security checklist self-tests

**Context:** the self-test commands from `FEAT-33a` need to run as a
CI gate, otherwise they rot.

**Requirements:**

- `tests/integration/test_security_checklist.py`.
- Run each self-test, assert pass.
- Add an `inv security-check` (or `make security-check`) target so
  the operator can run them locally.

**Testing:** run the test, confirm green. Introduce a deliberate
token leak in a log line, confirm the test fails with a clear
pointer.

**Estimated Effort:** 1-2h

### [TEST-09a1] Scale fixture generator, Sites and Devices

**Context:** the first window of the 50k-object scale fixture
lays the foundation, 10 Sites and 200 Devices spread across them.
Devices need their Manufacturer, DeviceType, DeviceRole, Site,
Location, Rack chain populated. Per-interface and per-IP
generation lives in `TEST-09a2` and `TEST-09a3`.

**Requirements:**

- Create `tests/fixtures/scale/generator.py` with
  `generate_sites_and_devices(out_dir, *, seed=42, n_sites=10,
  n_devices=200) -> None`.
- Deterministic, every call with the same seed produces
  byte-identical output.
- Emit `tests/fixtures/scale/01-sites.json` (10 Sites named
  `scale-hall-NN`).
- Emit `02-locations.json` (20 Locations, 2 per Site).
- Emit `03-racks.json` (40 Racks, 2 per Location).
- Emit `04-manufacturers.json` (3 entries, `cisco`, `juniper`,
  `arista`).
- Emit `05-device-types.json` (one per manufacturer).
- Emit `06-device-roles.json` (`access_switch`, `distribution_switches`,
  `core_router`).
- Emit `07-devices.json` (200 Devices, distributed evenly across
  Racks, role assigned by index modulo 3, name
  `scale-device-NNNN`).
- Use `random.Random(seed)` exclusively, no module-level random.

**Testing:** unit test in
`tests/unit/test_scale_generator_sites.py`. Generate twice with
the same seed, assert the seven JSON files are byte-identical.
Generate with different seeds, assert at least one Device's
Rack assignment differs (proves randomness is wired).

**Estimated Effort:** 1-2h

### [TEST-09a2] Scale fixture generator, Interfaces

**Context:** the second window generates 5k Interfaces, ~25 per
Device on average. Each Device gets a deterministic interface
count based on its role.

**Requirements:**

- Extend `tests/fixtures/scale/generator.py` with
  `generate_interfaces(out_dir, *, seed=42, n_total=5000) ->
  None`.
- Reads the Devices from `07-devices.json` written by `09a1`.
- Distribution, `access_switch` Devices get 24 interfaces
  (`Gi0/1` through `Gi0/24`). `distribution_switches` Devices
  get 12 interfaces (`ge-0/0/0` through `ge-0/0/11`).
  `core_router` Devices get 4 interfaces (`xe-0/0/0` through
  `xe-0/0/3`).
- Emit `08-interfaces.json` with the 5000 Interface payloads.
- Each Interface references its parent Device by name.
- Deterministic from the same seed.

**Testing:** unit test in
`tests/unit/test_scale_generator_interfaces.py`. Generate twice,
byte-identical. Confirm total count is 5000. Confirm every
access switch has 24 interfaces. Confirm every dist has 12.

**Estimated Effort:** 1-2h

### [TEST-09a3] Scale fixture generator, IP addresses and cables

**Context:** the third window generates 50k IPAddresses spread
across the 5k Interfaces, plus 1k Cables connecting access
switches to dist ports.

**Requirements:**

- Extend `tests/fixtures/scale/generator.py` with
  `generate_addressing_and_cabling(out_dir, *, seed=42,
  n_ips=50000, n_cables=1000) -> None`.
- Reads the Devices and Interfaces from `07-devices.json` and
  `08-interfaces.json`.
- Emit `09-vlans.json` (1 VLAN per Site, 10 total).
- Emit `10-prefixes.json` (one `/16` per Site, 10 total).
- Emit `11-ip-ranges.json` (one IP Range per Prefix, 10 total).
- Emit `12-ip-addresses.json` with 50000 IPAddresses,
  distributed approximately 10 per Interface, addresses drawn
  from the Site's `/16` Prefix.
- Emit `13-cables.json` with 1000 Cables, each connecting one
  access switch port to one dist port on the same Site. Use the
  polymorphic termination resolver from `INFRA-03h`.
- Deterministic from the same seed.

**Testing:** unit test in
`tests/unit/test_scale_generator_addressing.py`. Generate twice,
byte-identical. Confirm `12-ip-addresses.json` has 50000
entries. Confirm `13-cables.json` has 1000 entries with both
terminations referencing Interfaces on the same Site.

**Estimated Effort:** 1-2h

### [TEST-09b] Scale test runner with perf assertions

**Context:** the actual perf assertion.

**Requirements:**

- `tests/integration/test_scale.py`.
- Seed the scale fixture into a fresh source stack, run round-trip,
  assert wall time under 2x the design budget from
  `docs/05-export-import-workflow.md`.
- Runs nightly, not per-PR (use a `pytest.mark.nightly` marker that
  is excluded by default).

**Testing:** run the test in the nightly CI workflow, confirm green
on a healthy runner. Run on a deliberately throttled runner, confirm
the perf assertion fires with a clear message.

**Estimated Effort:** 1-2h

### [REL-01a] PyPI release workflow file

**Context:** `PLAN.md` Phase 9 exit.

**Requirements:**

- `.github/workflows/release.yml` triggered on tag matching `v*`.
- Steps: checkout, setup-python, install build tools, build wheel
  and sdist, upload as GitHub Release asset.
- Job runs only on `main` branch tags.

**Testing:** create a dry-run tag (`v0.0.1-rc.0`), push, confirm the
release job runs and produces a wheel + sdist as artefacts.

**Estimated Effort:** 1-2h

### [REL-01b] Sigstore signing and PyPI publish step

**Context:** trusted publisher path from `docs.pypi.org`.

**Requirements:**

- Extend `release.yml` with sigstore signing of the wheel + sdist.
- PyPI upload via OIDC token (no API token in secrets).
- Configure PyPI trusted publisher for the repo.
- Document the manual setup in `docs/security-review.md`.

**Testing:** publish a release candidate to TestPyPI first, confirm
the sigstore signatures verify. Confirm a regular `pip install
--index-url https://test.pypi.org/simple/ nbsnap==0.0.1rc0` works.

**Estimated Effort:** 1-2h

### [REL-02] Snapshot format CHANGELOG seed file

**Context:** the snapshot format has its own semver per
`docs/04-snapshot-format.md`.

**Requirements:**

- Create `docs/snapshot-format-CHANGELOG.md` following Keep a
  Changelog format.
- Pre-populate with one entry: `0.1.0, unreleased, initial format`.
- Cross-link from `docs/04-snapshot-format.md`.

**Testing:** self-review the file format against the Keep a
Changelog spec. Confirm the cross-link resolves.

**Estimated Effort:** 1-2h

---

## Future considerations

### Real-time sync via webhooks

Out of scope for v1 (per `goals.md` non-goal). When this becomes a
goal, the snapshot format's `_op` field (currently always `upsert`) and
the natural-key resolver give us most of what we need; the missing
piece is a webhook receiver mode that maps NetBox change events to
snapshot operations and ships them across a queue.

### Cross-major-version migration

Out of scope for v1. Pattern: pre-export field migration via the
`field_renames` table (`docs/frictions/06-netbox-version-drift.md` M5),
adopted into the snapshot format with explicit `_from_version` /
`_to_version` annotations.

### Image attachment blobs

`docs/frictions/08-install-local-references.md` M7 carries URLs + SHA
only. Future: `nbsnap blob push <snapshot>` that uploads the bytes via
a separate channel to a destination NetBox's media store. Requires a
NetBox-side endpoint we currently don't have; track upstream.

### GraphQL-native export

If `RES-06` confirms GraphQL is faster, future work: drop the REST
export path entirely. v1.0 keeps REST as the canonical path so the
tool runs against any NetBox 4.x even if GraphQL is disabled.

---

## Cut, ticket no longer planned

These tickets were dropped during the question-burndown enrichment
pass. Listed for traceability so the cross-references in
`PLAN.md` and the design docs can be cleaned up in a follow-up.

### [FEAT-13b] DNS resolution opt-in for webhook URLs (CUT)

Cut by Q17 / Q19 burndown answers. Under the network-only scope,
the install-local classifier narrows to a single string-comparison
rule on `IPAddress.dns_name`, no DNS resolution path remains. The
narrowed classifier lives in `FEAT-13a`. The flag-file writer in
`FEAT-13c` stays.

### [RES-05] Decide DNS resolution behaviour for install-local classification (CUT)

Cut by Q17 / Q19 burndown answers. The decision is moot once the
classifier no longer resolves DNS. See `FEAT-13a` for the
remaining string-comparison rule.

## Completed

(none yet)
