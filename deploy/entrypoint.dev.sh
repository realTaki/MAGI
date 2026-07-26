#!/bin/sh
# Dev-mode container entrypoint. Runs vite (HMR for the SPA) in
# the background, then execs `magi` as PID 1 so the container's
# lifecycle is tied to the Python process — vite crashing doesn't
# kill the container, but magi crashing does, and compose restarts
# the whole service.
#
# Auto-reload for Python is driven by MAGI_RELOAD=1 in
# docker-compose.dev.yml; uvicorn picks it up via NodeConfig.

set -eu
cd /app/magi/WebUI

# Auto-install new dependencies on container start. Vite reads the
# module graph at boot, so if a new dep was added to package.json
# (e.g. @tanstack/react-query) but the bind-mounted /app/magi/WebUI
# is from before that change, the runtime's node_modules is stale
# and Vite fails with "Failed to resolve import". Doing `npm install`
# here (instead of `npm ci`) means a small diff in package.json
# is satisfied without an image rebuild; if package.json hasn't
# changed, npm install short-circuits to a near-noop.
if [ -f package.json ]; then
  if [ -d node_modules ]; then
    if [ package.json -nt node_modules/.package-lock.json ]; then
      echo "[entrypoint] package.json newer than node_modules, running npm install"
      npm install --no-audit --no-fund --prefer-offline || {
        echo "[entrypoint] npm install failed; trying offline cache"
        npm install --no-audit --no-fund --prefer-offline --offline
      }
      touch node_modules/.package-lock.json
    fi
  else
    echo "[entrypoint] node_modules missing, running npm install"
    npm install --no-audit --no-fund --prefer-offline
  fi
fi

npm run dev -- --host 0.0.0.0 --port 42069 &
VITE_PID=$!
trap "kill $VITE_PID 2>/dev/null || true" EXIT INT TERM
exec magi
