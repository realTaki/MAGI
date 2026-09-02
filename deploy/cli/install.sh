#!/usr/bin/env bash
# Install MAGI for the openclaw-style single-machine deployment.
#
# This script installs MAGI and starts a usable local instance:
#
#  1. Verify the `magi` console script is on PATH (or invoke uv to
#     install the package into a user venv).
#  2. Provision Genesis on the first run, then start the first MAGI and WebUI.
#  3. Print the data root under the OS-specific MAGI path:
#       - Linux:   ~/.magi
#       - macOS:   ~/Documents/.magi
#       - Windows: ~/Documents/.magi  (resolved via $USERPROFILE)
# `magi start` is idempotent: later invocations preserve the existing
# Society and only recover processes that are not already running.
set -euo pipefail

HOST_WORKSPACE_DIR_DEFAULT() {
  case "$(uname -s)" in
    Darwin)  echo "$HOME/Documents/.magi" ;;
    Linux)   echo "$HOME/.magi" ;;
    MINGW*|MSYS*|CYGWIN*) echo "$USERPROFILE/Documents/.magi" ;;
    *) echo "$HOME/.magi" ;;
  esac
}

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
DATA_ROOT="${HOST_WORKSPACE_DIR:-$(HOST_WORKSPACE_DIR_DEFAULT)}"

log() { printf '\033[1;34m[magi-install]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[magi-install]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[magi-install]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "$1 is required on PATH"; }

# 1. Verify the package is installed.

if ! command -v magi >/dev/null 2>&1; then
  warn "'magi' is not on PATH. Attempting to install via uv ..."
  require_cmd uv
  ( cd "$REPO_ROOT/py-magi" && uv tool install --extra adam --extra eva . )
  if ! command -v magi >/dev/null 2>&1; then
    die "uv install finished but 'magi' is still not on PATH. Try: uv tool dir/bin"
  fi
  log "magi installed: $(command -v magi)"
else
  log "magi already installed: $(command -v magi)"
fi

# 2. Provision and start.  Keep the resolved path explicit so the CLI and the
# installer always operate on the same data root.
log "starting MAGI (data: $DATA_ROOT)"
HOST_WORKSPACE_DIR="$DATA_ROOT" magi start

cat <<EOF

[$(basename "$0")] MAGI is ready: http://127.0.0.1:42069
Data: $DATA_ROOT

Later use:

    magi start                    # safely start/recover the local instance
    magi node create --name eva-001
    magi node run --name eva-001

EOF
