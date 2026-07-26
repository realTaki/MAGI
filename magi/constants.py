"""MAGI hard-coded constants.

Everything in this file is a fixed value that **cannot** be
overridden by environment variables or database settings.
If you need to change one of these, you must edit this file
and rebuild.

For mutable configuration, see the ``settings`` table
(``magi/agent/db/models_setting.py``, read via
``magi.agent.db.settings.state_get``).
"""

from __future__ import annotations

# -- container filesystem layout -----------------------------------------
# The container bind-mounts the host workspace at /workspace.
# State (SQLite + migrations + session history) lives in a
# ``memories/`` subdirectory so the workspace root can hold
# user-facing artifacts (skills/, SOUL.md) alongside system
# data without collisions.
STATE_DIR: str = "/workspace/memories"
WORKSPACE_DIR: str = "/workspace"

# -- Web UI --------------------------------------------------------------
WEBUI_HOST: str = "0.0.0.0"
WEBUI_PORT: int = 42069

# -- defaults (fallback before DB is up) ---------------------------------
DEFAULT_LOG_LEVEL: str = "info"
