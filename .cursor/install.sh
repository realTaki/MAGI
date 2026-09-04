#!/usr/bin/env bash
# Cloud Agent install phase for MAGI.
#
# Idempotent, source-derived setup that runs after the repository is
# checked out. It prepares everything durable that a MAGI dev loop needs:
#
#   1. `uv` (the pinned Python package manager) — installed to
#      ~/.local/bin if it is not already on PATH.
#   2. The Python virtualenv (`.venv`) with every runtime + dev extra
#      (`adam`, `eva`, `dev`) resolved from the committed `uv.lock`.
#   3. The desktop UI production bundle (`desktop/ui/dist`) for Electron.
#
# No long-lived process is started here — per-boot service startup lives
# in `.cursor/start.sh`.
set -euo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[1;34m[magi-install]\033[0m %s\n' "$*" >&2; }

# nvm-provided node/npm are on the login-shell PATH; make them and any
# user-local tools reachable when this script runs in a bare shell too.
export PATH="$HOME/.local/bin:$PATH"
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 || true
fi

# 1. uv — the project's package manager and script runner.
if ! command -v uv >/dev/null 2>&1; then
  log "uv not found; installing to ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
log "uv: $(command -v uv) ($(uv --version))"

# 2. Python dependencies — all extras (adam + eva + dev) from uv.lock.
#    `uv sync` is idempotent and only touches the venv when the lock changes.
log "syncing Python dependencies (all extras)"
( cd py-magi && uv sync --all-extras )

log "syncing ASP server Python dependencies"
( cd magi-asp && uv sync --all-extras )

# 3. Desktop UI production bundle.
log "building desktop UI (npm ci && npm run build)"
( cd desktop/ui && npm ci && npm run build )

log "install complete"
