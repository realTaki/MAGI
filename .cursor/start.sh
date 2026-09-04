#!/usr/bin/env bash
# Cloud Agent start phase for MAGI.
#
# Per-boot runtime initialization. `magi start` is idempotent:
#
#   * On the first boot it provisions the root Society (Genesis) and the
#     first MAGI (eva-000) under ~/.magi.
#   * It then starts (or recovers) the eva-000 node runtime on :42070 as
#     detached, PID-file-managed processes, and returns.
#
# Re-running is safe: existing Society state is preserved and only
# processes that are not already alive are (re)started. Service logs live
# under ~/.magi/logs and ~/.magi/MAGI_Citizens/<name>/logs.
set -euo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_ROOT/py-magi"

export PATH="$HOME/.local/bin:$PATH"
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 || true
fi

uv run magi start
