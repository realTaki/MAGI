#!/usr/bin/env bash
# Install MAGI for the openclaw-style single-machine deployment.
#
# This script performs three things and then exits:
#
#  1. Verify the `magi` console script is on PATH (or invoke uv to
#     install the package into a user venv).
#  2. Materialise the data root under the OS-specific openclaw path:
#       - Linux:   ~/.magi
#       - macOS:   ~/Documents/.magi
#       - Windows: ~/Documents/.magi  (resolved via $USERPROFILE)
#  3. Print a one-page cheat sheet of the post-install commands.
#
# It does NOT start the runtime; that is `magi local start`. It does
# NOT register a service; that is `magi local install-service`. The
# intent is to leave the operator in control of when the daemon
# actually comes up.
set -euo pipefail

MAGI_DATA_ROOT_DEFAULT() {
  case "$(uname -s)" in
    Darwin)  echo "$HOME/Documents/.magi" ;;
    Linux)   echo "$HOME/.magi" ;;
    MINGW*|MSYS*|CYGWIN*) echo "$USERPROFILE/Documents/.magi" ;;
    *) echo "$HOME/.magi" ;;
  esac
}

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
DATA_ROOT="${MAGI_DATA_ROOT:-$(MAGI_DATA_ROOT_DEFAULT)}"

log() { printf '\033[1;34m[magi-install]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[magi-install]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[magi-install]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "$1 is required on PATH"; }

# 1. Verify the package is installed.

if ! command -v magi >/dev/null 2>&1; then
  warn "'magi' is not on PATH. Attempting to install via uv ..."
  require_cmd uv
  ( cd "$REPO_ROOT" && uv tool install --extra adam --extra eva . )
  if ! command -v magi >/dev/null 2>&1; then
    die "uv install finished but 'magi' is still not on PATH. Try: uv tool dir/bin"
  fi
  log "magi installed: $(command -v magi)"
else
  log "magi already installed: $(command -v magi)"
fi

# 2. Materialise the data root.

mkdir -p "$DATA_ROOT/control" "$DATA_ROOT/MAGIC" "$DATA_ROOT/MAGIS/local"
log "data root ready at: $DATA_ROOT"

# 3. Print the cheat sheet.

cat <<EOF

[$(basename "$0")] Done. Three things you can do next:

    magi local start              # foreground-friendly one-shot; opens browser
    magi local install-service    # register systemd user unit (Linux only)
    magi local status             # show registered runtimes + port allocation

Optional environment overrides:

    MAGI_DATA_ROOT=/some/path     # relocate the data root before 'start'
    MAGI_KUBECONFIG=...           # only relevant for the k8s deploy paths

EOF
