"""API-side path resolution helper.

The 24 files under :mod:`magi.channels.api` each read
``MAGI_STATE_DIR`` from the environment.  Centralising that lookup here
lets the Composition Root swap the resolution rule later (e.g. inject a
``LocalPathLayout`` for the Local Profile) without touching every router.

This is **not** a new public API: it is the same env-var contract the
modules used before, just exposed once so the K8s path stays bit-identical
when the launcher sets ``MAGI_STATE_DIR`` and the Local path can override
via the launcher-constructed layout in a later phase.
"""

from __future__ import annotations

import os

from magi.constants import STATE_DIR


def resolve_state_dir() -> str:
    """Return the state directory for the current API request.

    Order of resolution (matches the legacy inline pattern):

    1. ``MAGI_STATE_DIR`` env var when set (tests + Composition Root);
    2. ``magi.constants.STATE_DIR`` (K8s default, ``/workspace/memories``).
    """
    return os.environ.get("MAGI_STATE_DIR") or STATE_DIR