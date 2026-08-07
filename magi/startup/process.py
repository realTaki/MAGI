"""PID-file process supervision primitives.

Both local supervisors — :mod:`magi.startup.local` (one process per
MAGI) and :mod:`magi.startup.webui` (the single WebUI process) —
follow the same pattern: write a PID file on spawn, then read it back
to answer "is that process still up?". These two helpers are that
pattern's whole surface; they lived duplicated (byte-identical) in
both modules until this module claimed them.

Deliberately narrow: no spawning, no signalling, no path resolution
(that's :mod:`magi.startup.paths`). Just "read the file" and "is this
PID live", so both supervisors agree on what a stale PID file means.
"""

from __future__ import annotations

import os
from pathlib import Path


def read_pid(pid_path: Path) -> int | None:
    """Read a PID out of ``pid_path``, or ``None`` if unreadable.

    Every failure mode collapses to ``None`` — missing file, an
    unreadable one (permissions, a directory in its place), or
    garbage contents. Callers treat ``None`` as "not running",
    which is the safe reading: a PID file we can't parse tells us
    nothing about a live process.
    """
    if not pid_path.exists():
        return None
    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def is_alive(pid: int) -> bool:
    """Is ``pid`` a live process?

    ``os.kill(pid, 0)`` sends no signal — it only runs the kernel's
    existence-and-permission check. ``PermissionError`` counts as
    alive: the process exists, it just belongs to another user.

    Note the PID-reuse caveat inherent to this check — a recycled PID
    reads as alive. Both supervisors accept that; the PID files are
    rewritten on every spawn, so the window is small.
    """
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


__all__ = ["read_pid", "is_alive"]
