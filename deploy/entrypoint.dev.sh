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
cd /web
npm run dev -- --host 0.0.0.0 --port 42069 &
VITE_PID=$!
trap "kill $VITE_PID 2>/dev/null || true" EXIT INT TERM
exec magi
