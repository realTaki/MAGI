#!/usr/bin/env bash
# Prepare current locked DSH workspaces without starting services or changing PATH.
set -euo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_ROOT"
log() { printf '[magi-install] %s\n' "$*" >&2; }
RUNTIME_BIN="$REPO_ROOT/runtime/bin"
RUNTIME_NPM="$REPO_ROOT/runtime/npm/node_modules/npm/bin/npm-cli.js"

# An explicit pair must be complete. Never guess an npm executable from PATH.
if [[ -n "${MAGI_NODE:-}" || -n "${MAGI_NPM_CLI:-}" ]]; then
  if [[ -z "${MAGI_NODE:-}" || -z "${MAGI_NPM_CLI:-}" ]]; then
    log 'Set both MAGI_NODE and MAGI_NPM_CLI to absolute MAGI-owned tool paths.'
    exit 1
  fi
  BOOTSTRAP_NODE="$MAGI_NODE"
  BOOTSTRAP_NPM="$MAGI_NPM_CLI"
elif [[ -x "$RUNTIME_BIN/node" && -f "$RUNTIME_NPM" ]]; then
  BOOTSTRAP_NODE="$RUNTIME_BIN/node"
  BOOTSTRAP_NPM="$RUNTIME_NPM"
elif [[ -x /Applications/MAGI.app/Contents/Resources/runtime/bin/node && -f /Applications/MAGI.app/Contents/Resources/runtime/npm/node_modules/npm/bin/npm-cli.js ]]; then
  BOOTSTRAP_NODE=/Applications/MAGI.app/Contents/Resources/runtime/bin/node
  BOOTSTRAP_NPM=/Applications/MAGI.app/Contents/Resources/runtime/npm/node_modules/npm/bin/npm-cli.js
else
  log 'MAGI-owned runtime missing. Supply MAGI_NODE and MAGI_NPM_CLI from a MAGI packaged runtime, or use the documented CI provisioning stage.'
  exit 1
fi
if [[ "$BOOTSTRAP_NODE" != /* || "$BOOTSTRAP_NPM" != /* || ! -x "$BOOTSTRAP_NODE" || ! -f "$BOOTSTRAP_NPM" ]]; then
  log 'MAGI-owned Node and npm CLI must be existing absolute paths.'
  exit 1
fi
"$BOOTSTRAP_NODE" -e 'if (Number(process.versions.node.split(".")[0]) < 24) { console.error("MAGI requires Node >=24"); process.exit(1); }'
BOOTSTRAP_PATH="$(dirname -- "$BOOTSTRAP_NODE"):$PATH"
log "installing locked workspace dependencies with $BOOTSTRAP_NODE"
PATH="$BOOTSTRAP_PATH" "$BOOTSTRAP_NODE" "$BOOTSTRAP_NPM" ci

# Node is owned and pinned by the root, independent of archived workspaces.
PROJECT_NODE="$("$BOOTSTRAP_NODE" -e 'const path = require("node:path"); const manifest = require.resolve("node/package.json", { paths: [process.cwd()] }); console.log(path.join(path.dirname(manifest), "bin", "node"));')"
log 'preparing the MAGI-owned runtime'
PATH="$BOOTSTRAP_PATH" MAGI_NPM_CLI="$BOOTSTRAP_NPM" "$PROJECT_NODE" scripts/prepare-runtime.mjs
log 'validating DSH source with the MAGI-owned runtime'
PATH="$RUNTIME_BIN:$PATH" "$RUNTIME_BIN/node" scripts/check-dsh.mjs
log 'install complete; no services started'
