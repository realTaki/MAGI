"""MAGI launcher constants — fixed values that don't change at runtime.

These are the *truly* fixed values: web bind defaults, system log
level default.  Path-related constants have moved to
:mod:`magi.launcher.paths` (``workspace_dir()``, ``state_dir()``).

Replaces the legacy ``magi/constants.py`` file, which conflated
path-resolution helpers with hard-coded values.
"""

from __future__ import annotations

# -- Web UI -------------------------------------------------------------------

WEBUI_HOST: str = "0.0.0.0"
WEBUI_PORT: int = 42069

# -- defaults (fallback before DB is up) -----------------------------------

DEFAULT_LOG_LEVEL: str = "info"


__all__ = [
    "WEBUI_HOST",
    "WEBUI_PORT",
    "DEFAULT_LOG_LEVEL",
]
