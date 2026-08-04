#!/bin/sh
# Dev-mode container entrypoint (single-node k8s dev mode only).
# Runs vite (HMR for the SPA) in the background, then execs `magi` as
# PID 1 so the container's lifecycle is tied to the Python process —
# vite crashing doesn't kill the container, but magi crashing does,
# and the kubelet restarts the whole Pod.
#
# Auto-reload for Python is driven by MAGI_RELOAD=1 in the dev-eva00
# overlay's ConfigMap; uvicorn picks it up via NodeConfig.

set -eu
cd /app/magi/WebUI

# Auto-install dependencies into the writable node_modules volume. The source
# checkout is read-only in the Kind dev overlay, so use `npm ci`: unlike npm
# install it does not attempt to rewrite package-lock.json in that checkout.
if [ -f package.json ]; then
  if [ -d node_modules ]; then
    if [ ! -f node_modules/.package-lock.json ] || [ package.json -nt node_modules/.package-lock.json ]; then
      echo "[entrypoint] package metadata newer than node_modules, running npm ci"
      npm ci --no-audit --no-fund --prefer-offline || {
        echo "[entrypoint] npm ci failed; trying offline cache"
        npm ci --no-audit --no-fund --prefer-offline --offline
      }
      touch node_modules/.package-lock.json
    fi
  else
    echo "[entrypoint] node_modules missing, running npm ci"
    npm ci --no-audit --no-fund --prefer-offline
  fi
fi

npm run dev -- --host 0.0.0.0 --port 42069 &
VITE_PID=$!
trap "kill $VITE_PID 2>/dev/null || true" EXIT INT TERM
exec magi "$@"
