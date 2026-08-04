"""MAGI hard-coded constants.

DEPRECATED — being phased out. Path-related constants have moved to
:mod:`magi.launcher.paths` (``workspace_dir()``, ``state_dir()``).
The remaining truly-constant values (``WEBUI_*``, ``DEFAULT_LOG_LEVEL``)
will move to :mod:`magi.launcher.constants` in Phase C / F.

This file stays as a thin re-export shim until all importers are
migrated; the migration deletes it in Phase F.

For mutable configuration, see the ``settings`` table
(``magi/db/models_setting.py``, read via
``magi.db.settings.state_get``).
"""

from __future__ import annotations

# -- container filesystem layout -----------------------------------------
# DEPRECATED.  Path resolution moved to :mod:`magi.launcher.paths`.
# These module-level constants are kept as a temporary shim for code
# that imports ``STATE_DIR`` / ``WORKSPACE_DIR`` as strings; they will
# be deleted in Phase F.  New code should call ``launcher.paths.state_dir()``
# / ``launcher.paths.workspace_dir()`` instead.
from magi.launcher.paths import state_dir as _sd, workspace_dir as _wd

STATE_DIR: str = str(_sd())
WORKSPACE_DIR: str = str(_wd())

# -- Web UI --------------------------------------------------------------
WEBUI_HOST: str = "0.0.0.0"
WEBUI_PORT: int = 42069

# -- defaults (fallback before DB is up) ---------------------------------
DEFAULT_LOG_LEVEL: str = "info"
