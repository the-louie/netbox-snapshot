# Security review checklist (FEAT-33a1/a2)

Before each release, work through every section below and tick the
boxes. Any unchecked box blocks the release.

## Section 1, secret handling

* [ ] No API token, password, or `SECRET_KEY` is logged at any
      level, including DEBUG. `NetboxHTTP.__repr__` masks tokens
      and `log.JsonFormatter` does not surface kwargs.
* [ ] No secret is committed to the repo. `git grep` the candidate
      values from `.env` against the codebase before each release.
* [ ] `.env` and any `*.token` artefact are gitignored.

## Section 2, TLS posture

* [ ] TLS verification is on by default.
      `NetboxHTTP(verify_tls=True)` is the constructor default.
* [ ] The only opt-out path is the explicit
      `--no-verify-tls` flag, with a one-line WARNING log when it
      fires.
* [ ] The destination NetBox in production deployments uses TLS
      verification on, no exceptions.

## Section 3, install-local exclusion correctness

* [ ] The install-local classifier covers `IPAddress.dns_name`
      equality with the source host (per network-only scope).
* [ ] The flag log (`flags.jsonl`) records every excluded row so
      the operator can audit the omissions.
* [ ] A round-trip test confirms that re-export from the
      destination does not surface the source host's `dns_name`.

## Section 4, source-readonly invariant (FEAT-33a2)

* [ ] `NetboxHTTP.from_env("source")` returns a client with
      `allow_writes=False`.
* [ ] Direct construction with `base_url == NB_SOURCE_URL` forces
      `allow_writes=False` regardless of the kwarg.
* [ ] `tests/integration/test_source_readonly_e2e.py` is in the
      CI integration job and the socket counter assertion is
      green.

### Static self-tests

The static self-tests below run as part of the release CI; the
release script aborts on any failure.

* `pytest tests/unit/test_guard_helper.py` (8 cases)
* `pytest tests/unit/test_http_client_is_source.py` (3 cases)
* `pytest tests/integration/test_source_readonly_e2e.py` (5 cases)

The combined assertion: at no point during the test runs is a
non-GET request issued against `NB_SOURCE_URL`.
