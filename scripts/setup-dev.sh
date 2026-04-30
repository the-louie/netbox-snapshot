#!/usr/bin/env bash
#
# Idempotent dev environment setup for nbsnap.
#
# Run this on the host (outside any container) from the repo root:
#
#     ./scripts/setup-dev.sh
#
# What it does:
#   1. Verifies python3 >= 3.11.
#   2. Creates .venv if missing.
#   3. Installs / updates the editable install + dev extras
#      whenever pyproject.toml's hash changes since the last run.
#   4. Optionally installs pre-commit hooks the first time it runs.
#   5. Prints a short next-steps summary.
#
# Safe to re-run. The pyproject hash stamp lives at
# .venv/.nbsnap-pyproject.sha256 so a "no change" run is a fast
# no-op.

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Colour output only when stdout is a TTY, scripts piped through CI
# logs do not need the escape noise.
if [ -t 1 ]; then
    GREEN=$'\033[32m'
    YELLOW=$'\033[33m'
    RED=$'\033[31m'
    BOLD=$'\033[1m'
    RESET=$'\033[0m'
else
    GREEN=""; YELLOW=""; RED=""; BOLD=""; RESET=""
fi

say()  { printf "%s==>%s %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%swarn:%s %s\n" "$YELLOW" "$RESET" "$*" >&2; }
die()  { printf "%serror:%s %s\n" "$RED"  "$RESET" "$*" >&2; exit 1; }

# Move to repo root regardless of where the script was invoked from.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR=".venv"
STAMP_FILE="$VENV_DIR/.nbsnap-pyproject.sha256"
MIN_PY_MAJOR=3
MIN_PY_MINOR=11

# ---------------------------------------------------------------------------
# Step 1, find a working python3 binary >= 3.11
# ---------------------------------------------------------------------------

find_python() {
    # Try several names, take the first one >= 3.11.
    for candidate in python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            local version
            version="$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
            local major="${version%.*}"
            local minor="${version#*.}"
            if [ "$major" -gt "$MIN_PY_MAJOR" ] || \
               { [ "$major" -eq "$MIN_PY_MAJOR" ] && [ "$minor" -ge "$MIN_PY_MINOR" ]; }; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
    die "no python3 >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} found on PATH; install one and re-run"
fi
say "using $PY ($("$PY" --version))"

# ---------------------------------------------------------------------------
# Step 2, create venv if missing
# ---------------------------------------------------------------------------

if [ ! -d "$VENV_DIR" ]; then
    say "creating venv at $VENV_DIR"
    "$PY" -m venv "$VENV_DIR"
else
    say "venv already present at $VENV_DIR"
fi

# Use the venv's interpreter from here on so we never accidentally
# install into the system Python. We deliberately invoke pip via
# `python -m pip` rather than the `.venv/bin/pip` wrapper script,
# the wrapper carries an absolute shebang line that breaks the
# moment the venv is relocated (a venv created at /workspace inside
# a container, then accessed on the host where /workspace does not
# exist, is the canonical case).
VENV_PY="$VENV_DIR/bin/python"

# Detect a broken venv up front. Three failure modes share the
# same fix (recreate):
#   1. The Python symlink target was removed (`python -c "import sys"` fails).
#   2. The venv was created at a different absolute path and the
#      pip wrapper's shebang now points nowhere ("python -m pip"
#      fails because the bootstrap fails).
#   3. The bin directory was partly deleted.
# All three are unrecoverable without a recreate, so we do that
# rather than asking the operator to.
venv_broken=0
if [ ! -x "$VENV_PY" ]; then
    venv_broken=1
elif ! "$VENV_PY" -c "import sys" >/dev/null 2>&1; then
    venv_broken=1
elif ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    venv_broken=1
fi

if [ "$venv_broken" -eq 1 ]; then
    warn "venv at $VENV_DIR is broken (interpreter or pip cannot run)"
    warn "common cause: the venv was created at a different absolute path"
    warn "             (e.g. inside a container) and the bin/ shebangs no longer resolve"
    say  "recreating venv from scratch"
    rm -rf "$VENV_DIR"
    "$PY" -m venv "$VENV_DIR"
    # Stamp file lives inside .venv so it is gone too; the next
    # block will run a full install.
fi

if [ ! -x "$VENV_PY" ]; then
    die "venv interpreter missing at $VENV_PY after recreate; investigate"
fi

# ---------------------------------------------------------------------------
# Step 3, decide whether to (re)install dependencies
# ---------------------------------------------------------------------------
#
# Two independent signals drive the decision:
#
#   1. pyproject.toml hash vs the stamp file (what we last installed).
#   2. `import nbsnap` actually working in the venv right now.
#
# Either signal flipping forces a reinstall. Trusting only the hash
# leaves the script vulnerable to stale `.venv` directories whose
# stamp survived a botched install or a cross-host copy.

current_hash="$(sha256sum pyproject.toml | awk '{print $1}')"
stored_hash=""
if [ -f "$STAMP_FILE" ]; then
    stored_hash="$(cat "$STAMP_FILE")"
fi

# Live import check, runs in the venv. Captures the verdict in a
# variable so the log line below can explain itself.
if "$VENV_PY" -c "import nbsnap" >/dev/null 2>&1; then
    nbsnap_importable=1
else
    nbsnap_importable=0
fi

if [ "$current_hash" != "$stored_hash" ]; then
    reason="pyproject.toml changed (or first install)"
    need_install=1
elif [ "$nbsnap_importable" -eq 0 ]; then
    reason="nbsnap is not importable from the venv (stale stamp or partial install)"
    need_install=1
else
    need_install=0
fi

if [ "$need_install" -eq 1 ]; then
    say "$reason; refreshing dependencies"
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install --quiet -e ".[dev]"
    echo "$current_hash" > "$STAMP_FILE"
    say "dependencies are up to date"
else
    say "pyproject.toml hash matches and nbsnap imports cleanly, no-op"
fi

# ---------------------------------------------------------------------------
# Step 4, pre-commit hooks (best effort, first run only)
# ---------------------------------------------------------------------------

if [ -d .git ] && [ ! -f .git/hooks/pre-commit.installed-by-nbsnap-setup ]; then
    # Invoke via `python -m pre_commit` so the shebang of
    # .venv/bin/pre-commit (which can be broken on a relocated venv)
    # does not bite us here.
    if "$VENV_PY" -m pre_commit install >/dev/null 2>&1; then
        touch .git/hooks/pre-commit.installed-by-nbsnap-setup
        say "pre-commit hooks installed"
    else
        warn "pre-commit install failed; run 'source .venv/bin/activate && pre-commit install' manually"
    fi
fi

# ---------------------------------------------------------------------------
# Step 5, quick smoke check
# ---------------------------------------------------------------------------

say "verifying the editable install imports cleanly"
if ! "$VENV_PY" -c "import nbsnap; print(f'nbsnap {nbsnap.__version__} importable from venv')"; then
    die "post-install smoke check failed; remove $VENV_DIR and re-run for a clean rebuild"
fi

# ---------------------------------------------------------------------------
# Step 6, next-steps summary
# ---------------------------------------------------------------------------

cat <<EOF

${BOLD}next steps${RESET}

  Activate the venv:
      source .venv/bin/activate

  Run the unit suite (fast, no docker):
      pytest tests/unit -q

  Bring up the two-NetBox test stack (needs docker on the host):
      make stack-up stack-wait stack-seed

  Run the integration suite (with the stacks up):
      pytest tests/integration -q

  End-to-end round-trip against the test stacks:
      nbsnap verify \\
          --source-url http://localhost:8080 \\
          --source-token 0123456789abcdef0123456789abcdef01234567 \\
          --dest-url http://localhost:8081 \\
          --dest-token abcdef0123456789abcdef0123456789abcdef01

  Teardown:
      make stack-down

EOF
