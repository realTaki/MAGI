"""OS detection helpers for the Local launcher.

Tiny, dependency-free.  Phase 6's ``magi local start`` uses these to
decide whether the launcher can ``open`` a browser tab, where to
write the PID file, and how to interpret the supervisor's exit codes.
"""

from __future__ import annotations

import sys
from typing import Literal

PlatformName = Literal["macos", "linux", "windows", "other"]


def current_platform() -> PlatformName:
    """Return the platform family as a stable string."""
    name = sys.platform
    if name == "darwin":
        return "macos"
    if name.startswith("linux"):
        return "linux"
    if name == "win32":
        return "windows"
    return "other"


def open_browser(url: str) -> None:
    """Best-effort open ``url`` in the OS default browser.

    Failures are swallowed; the launcher never crashes on missing
    browser support.
    """
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:
        pass


def supports_posix_pgid() -> bool:
    """``pgid`` / ``os.setpgrp`` are POSIX-only; Windows launches without one."""
    return os.name == "posix"


import os


__all__ = ["current_platform", "open_browser", "supports_posix_pgid", "PlatformName"]
