# nbsnap, NetBox Portable Snapshot

> ## ABSOLUTELY NO CHANGES ARE ALLOWED ON THE SOURCE NETBOX
>
> The instance at `NB_SOURCE_URL` (`host.docker.internal:8443`) is the
> **production** NetBox. Every interaction with it must be **read-only**.
>
> - **No `POST`, `PATCH`, `PUT`, or `DELETE`** requests against
>   `NB_SOURCE_URL`, ever, under any circumstance.
> - The `nbsnap export` command is the only sanctioned way to touch the
>   source, it issues `GET` only.
> - The HTTP client must refuse non-GET requests when the configured
>   `base_url` matches `NB_SOURCE_URL`, this is a guard rail, not a
>   convention.
> - Test fixtures, seeders, and integration suites target the local
>   netbox-docker test stack on ports `8080`/`8081`, never the production
>   source.
> - Writes flow source -> snapshot file -> destination. The destination
>   (`NB_DESTINATION_URL`) is the only NetBox that ever receives writes
>   from this tool.
>
> Violating this is a production incident. When in doubt, do nothing
> against the source and ask the operator first.
>
> See [`CLAUDE.md`](CLAUDE.md) for the canonical version of this
> banner and the full agent context. Treat that file as the source of
> truth, this README copy exists so a human running `cat README.md`
> sees the constraint before any workflow guidance.

## What is this?

`nbsnap` produces a portable, machine-readable abstraction of a running
NetBox instance and re-imports it into another instance, over the
REST API only. The intended workflow is

```
NetBox A  ──[ nbsnap export ]──►  snapshot/  ──[ nbsnap import ]──►  NetBox B
```

so two NetBoxes that live on isolated networks (no shared database,
no `pg_dump`/`psql` access) can share their modelled network. Scope
is the network model only, see the second banner in
[`CLAUDE.md`](CLAUDE.md) for the precise inclusion list.

## Status

The project is in the **design phase**. Phase 0, Foundation, is the
current focus. See [`PLAN.md`](PLAN.md) for the phasing schedule and
exit criteria, and [`TODO.md`](TODO.md) for the current backlog.

## Documentation

| Document | What it covers |
| :--- | :--- |
| [`PLAN.md`](PLAN.md) | Phase definitions, exit criteria, risks. |
| [`goals.md`](goals.md) | Goals, in-scope and out-of-scope decisions. |
| [`docs/INDEX.md`](docs/INDEX.md) | Index of design documents. |
| [`docs/frictions/00-overview.md`](docs/frictions/00-overview.md) | Friction-area deep-dives, the hard parts. |
| [`CLAUDE.md`](CLAUDE.md) | Agent context, also carries the production-read-only and scope banners. |

## Quick start

`nbsnap` reads connection settings from environment variables. Place
them in `/workspace/.env` (gitignored) or export them in your shell.
The four-variable scheme is

| Role | URL var | Token var |
| :--- | :--- | :--- |
| Source (PRODUCTION, READ-ONLY) | `NB_SOURCE_URL` | `NB_SOURCE_TOKEN` |
| Destination | `NB_DESTINATION_URL` | `NB_DESTINATION_TOKEN` |

The CLI shape is one endpoint per invocation:

```bash
nbsnap export --url "$NB_SOURCE_URL"      --token "$NB_SOURCE_TOKEN"      --out ./snapshot/
nbsnap import --url "$NB_DESTINATION_URL" --token "$NB_DESTINATION_TOKEN" --in  ./snapshot/
```

The legacy single-endpoint names `NB_URL` and `NB_TOKEN` are still
accepted as fall-backs so existing `__reference/nb2kea/` scripts keep
working.

TLS verification is on by default. The local
`host.docker.internal:8443` endpoint uses a self-signed certificate
and requires `--no-verify-tls`. The public destination keeps
verification on.

## Development

The fastest path: run the idempotent setup script. It verifies a
Python 3.11+ interpreter, creates `.venv`, installs nbsnap editable
plus the dev extras whenever `pyproject.toml` has changed, and
installs the pre-commit hooks the first time:

```bash
./scripts/setup-dev.sh
source .venv/bin/activate
```

The script re-runs cheaply, a second invocation is a no-op when
nothing has changed in `pyproject.toml`. The hash stamp lives at
`.venv/.nbsnap-pyproject.sha256` so a CI-style "always run setup"
costs nothing on the happy path.

Manual equivalent if you prefer to see every step:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
pre-commit install        # ruff and formatting hooks at commit time
```

The `.venv` is gitignored. `requests` and `responses` (test mocking)
are pulled in by the editable install. CI uses the same install
step so the local and remote toolchains stay in lockstep.

### Running the test suite

```bash
pytest tests/unit -q                  # fast, no docker needed
make stack-up stack-wait stack-seed   # spin up the two NetBox stacks
pytest tests/integration -q           # integration suite
make stack-down                       # teardown
```

End-to-end round-trip against the test stacks:

```bash
nbsnap verify \
    --source-url http://localhost:8080 \
    --source-token 0123456789abcdef0123456789abcdef01234567 \
    --dest-url http://localhost:8081 \
    --dest-token abcdef0123456789abcdef0123456789abcdef01
```

CI runs the same `ruff check .`, `ruff format --check .`, and
`mypy src/` commands locally available from the `dev` group. See
[`TODO.md`](TODO.md) `INFRA-04*` for the CI workflow and
`.pre-commit-config.yaml` for the hook list.
