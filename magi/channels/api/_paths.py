"""API-side path resolution helper.

Every ``magi.channels.api`` router reads its state directory through
:func:`resolve_state_dir`.  The single source of the path is
:mod:`magi.launcher.paths`; this module is just the legacy indirection
so tests continue to work while the actual lookup obeys
``MAGI_WORKSPACE_DIR``.

This module is scheduled for deletion in Phase F once every
``magi.channels.api`` router calls ``get_bus()`` instead of resolving
``state_dir`` locally.
"""

from __future__ import annotations

from magi.launcher.paths import state_dir


def resolve_state_dir() -> str:
    """Return the state directory for the current API request.

    Reads ``MAGI_WORKSPACE_DIR`` (deployer-supplied) and appends the
    canonical ``memories/`` subdirectory.  See :mod:`magi.launcher.paths`.
    """
    return str(state_dir())