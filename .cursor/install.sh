#!/usr/bin/env bash
# Prepare the current Node workspace, without starting services or packaging Electron.
# A fresh checkout needs Node >=24 and npm already available (optionally via nvm).
# That initial bootstrap only installs the lockfile; builds use MAGI-owned Node.
set -euo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '[magi-install] %s\n' "$*" >&2; }
RUNTIME_BIN="$REPO_ROOT/apps/shell/runtime/bin"
RUNTIME_NPM="$REPO_ROOT/apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js"

if [[ -x "$RUNTIME_BIN/node" && -f "$RUNTIME_NPM" ]]; then
  BOOTSTRAP_NODE="$RUNTIME_BIN/node"
  BOOTSTRAP_NPM="$RUNTIME_NPM"
else
  # Reuse nvm only if Node >=24 is not already available. Never install nvm,
  # change shell startup files, or silently download a new bootstrap runtime.
  if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)'; then
    NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [[ -s "$NVM_DIR/nvm.sh" ]]; then
      # shellcheck disable=SC1091
      . "$NVM_DIR/nvm.sh" --no-use
      nvm use 24
    fi
  fi
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    log 'Node >=24 and npm are required for the initial bootstrap.'
    exit 1
  fi
  BOOTSTRAP_NODE="$(command -v node)"
  BOOTSTRAP_NPM="$(npm root -g)/npm/bin/npm-cli.js"
fi

"$BOOTSTRAP_NODE" -e 'if (Number(process.versions.node.split(".")[0]) < 24) { console.error("MAGI requires Node >=24"); process.exit(1); }'
BOOTSTRAP_PATH="$(dirname -- "$BOOTSTRAP_NODE"):$PATH"
log "installing locked workspace dependencies with $BOOTSTRAP_NODE"
PATH="$BOOTSTRAP_PATH" "$BOOTSTRAP_NODE" "$BOOTSTRAP_NPM" ci

# The shell's node dependency is hoisted by the root npm workspace. Resolve it
# from the shell manifest, then use that project-owned binary to prepare runtime.
PROJECT_NODE="$("$BOOTSTRAP_NODE" -e 'const path = require("node:path"); const manifest = require.resolve("node/package.json", { paths: [path.resolve("apps/shell")] }); console.log(path.join(path.dirname(manifest), "bin", "node"));')"
log 'preparing the MAGI-owned runtime'
PATH="$BOOTSTRAP_PATH" npm_execpath="$BOOTSTRAP_NPM" "$PROJECT_NODE" apps/shell/scripts/prepare-runtime.mjs

log 'building the workspace with the MAGI-owned runtime'
PATH="$RUNTIME_BIN:$PATH" "$RUNTIME_BIN/node" "$RUNTIME_NPM" run build
log 'install complete; no services started'
