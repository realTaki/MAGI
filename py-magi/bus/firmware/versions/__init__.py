"""Firmware schema revisions and their current public version."""

from __future__ import annotations

import re
from pathlib import Path

_REVISION_FILE = re.compile(r"(?P<version>\d+\.\d+\.\d+)\.py$")


def current_version() -> str:
    """Return the latest firmware revision shipped with this BUS package."""
    revisions = [
        tuple(map(int, match.group("version").split(".")))
        for path in Path(__file__).parent.glob("*.py")
        if (match := _REVISION_FILE.fullmatch(path.name))
    ]
    if not revisions:
        raise RuntimeError("BUS firmware has no schema revisions")
    return ".".join(map(str, max(revisions)))


__version__ = current_version()

__all__ = ["__version__", "current_version"]
