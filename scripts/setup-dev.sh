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
# install into the system Python.
VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
if [ ! -x "$VENV_PY" ]; then
    die "venv interpreter missing at $VENV_PY; remove $VENV_DIR and re-run"
fi

# ---------------------------------------------------------------------------
# Step 3, install / update dependencies when pyproject.toml changed
# ---------------------------------------------------------------------------

current_hash="$(sha256sum pyproject.toml | awk '{print $1}')"
stored_hash=""
if [ -f "$STAMP_FILE" ]; then
    stored_hash="$(cat "$STAMP_FILE")"
fi

if [ "$current_hash" != "$stored_hash" ]; then
    say "pyproject.toml changed (or first install); refreshing dependencies"
    "$VENV_PIP" install --quiet --upgrade pip
    "$VENV_PIP" install --quiet -e ".[dev]"
    echo "$current_hash" > "$STAMP_FILE"
    say "dependencies are up to date"
else
    say "pyproject.toml hash matches last install, no-op"
fi

# ---------------------------------------------------------------------------
# Step 4, pre-commit hooks (best effort, first run only)
# ---------------------------------------------------------------------------

if [ -d .git ] && [ ! -f .git/hooks/pre-commit.installed-by-nbsnap-setup ]; then
    if "$VENV_DIR/bin/pre-commit" install >/dev/null 2>&1; then
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
"$VENV_PY" -c "import nbsnap; print(f'nbsnap {nbsnap.__version__} importable from venv')"

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
