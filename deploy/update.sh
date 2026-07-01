#!/usr/bin/env bash
# update.sh — pull latest from git and refresh the running stack.
#
# Usage:
#   ./update.sh [dev|prod]    # default: dev
#
# Dev mode (deploy/docker-compose.dev.yml override):
#   - source is bind-mounted from the host into the container
#   - uvicorn (MAGI_RELOAD=1) auto-reloads on .py file changes
#   - vite HMR handles .tsx / .css changes in the browser
#   - so after `git pull` the *next* request to the running
#     container picks up the new code automatically — no
#     container restart needed
#
# Prod mode (deploy/docker-compose.yml):
#   - the source is baked into the image at build time
#   - need to rebuild the image + recreate the container
#   - rebuild takes 1-2 minutes; the previous image layer is
#     replaced
#
# Both modes run `git pull --rebase --autostash` so any local
# edits in the working tree are stashed across the pull and
# reapplied on top — no "your local changes would be overwritten"
# aborts. Set UPD_REBASE=0 to use plain merge if you prefer.
#
# Make this script executable:  chmod +x deploy/update.sh

set -euo pipefail

# Anchor to the script's directory (deploy/) then cd to the
# repo root so `git pull` and the docker compose commands both
# work regardless of where the user invoked the script from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

mode="${1:-dev}"

if [ "$mode" != "dev" ] && [ "$mode" != "prod" ]; then
  echo "usage: $0 [dev|prod]" >&2
  exit 1
fi

echo ">>> mode: $mode"
echo ">>> repo: $(pwd)"
echo

echo ">>> git pull"
if [ "${UPD_REBASE:-1}" = "1" ]; then
  git pull --rebase --autostash
else
  git pull
fi

echo
if [ "$mode" = "prod" ]; then
  echo ">>> this will rebuild the adam image (~1-2 min). continue? [y/N]"
  read -r ans
  if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then
    echo "aborted"
    exit 0
  fi
  echo ">>> rebuilding adam image"
  docker compose -f deploy/docker-compose.yml build adam
  echo ">>> recreating adam container"
  docker compose -f deploy/docker-compose.yml up -d adam
else
  echo ">>> dev mode: uvicorn + vite pick up the new code on the"
  echo "    next request — no container restart needed."
  echo "    (uvicorn: MAGI_RELOAD=1 in docker-compose.dev.yml;"
  echo "     vite: HMR on by default)"
fi

echo
echo ">>> done"
