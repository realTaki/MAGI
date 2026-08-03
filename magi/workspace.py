"""Shared filesystem location helpers for one MAGI runtime."""

from __future__ import annotations

import os
from pathlib import Path


def workspace_root(state_dir: str | os.PathLike[str]) -> Path:
    """Return the workspace that owns the runtime state directory."""
    return Path(state_dir).parent
