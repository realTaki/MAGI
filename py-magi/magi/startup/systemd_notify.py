"""systemd sd_notify(3) wrapper — zero-dep, opt-in by environment.

Used by the runtime's watchdog ping loop.  Activates only when systemd
passed ``$NOTIFY_SOCKET`` (and optionally ``$WATCHDOG_USEC``); outside
a systemd-managed service every call is a no-op.  This keeps the
runtime safe to launch directly (``python -m magi node run``) without
having to special-case ``$NOTIFY_SOCKET=``-set-but-no-daemon errors.

Wire format per sd_notify(3):

    "READY=1\n"        — service finished startup
    "WATCHDOG=1\n"     — keepalive tick
    "STOPPING=1\n"     — clean shutdown about to begin

All frames are written to ``$NOTIFY_SOCKET`` as a single ``SOCK_DGRAM``
message; the socket is unlinked at process exit.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

_notify_socket_path: str | None = None


def _resolve_socket() -> str | None:
    """Return the ``$NOTIFY_SOCKET`` path or ``None`` if systemd isn't there."""
    global _notify_socket_path
    if _notify_socket_path is not None:
        return _notify_socket_path
    raw = os.environ.get("NOTIFY_SOCKET")
    if not raw:
        return None
    # systemd prepends ``@`` for abstract-namespace sockets; the kernel
    # already understands the literal ``\0``-prefixed path.  Leave it as-is
    # rather than translating — passing the literal string works on Linux.
    if not raw.startswith("@") and not Path(raw).exists():
        return None
    _notify_socket_path = raw
    return raw


def notify(state: str) -> bool:
    """Send one ``sd_notify`` frame.  Returns ``True`` iff systemd acked it."""
    sock_path = _resolve_socket()
    if sock_path is None:
        return False
    addr = "\0" + sock_path[1:] if sock_path.startswith("@") else sock_path
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(state.encode("utf-8"), addr)
    except OSError:
        return False
    return True


def watchdog_usec() -> int | None:
    """Return ``$WATCHDOG_USEC`` if systemd started us with one."""
    raw = os.environ.get("WATCHDOG_USEC")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def announce_ready() -> None:
    """Tell systemd the service finished startup.  No-op outside systemd."""
    notify("READY=1")


def announce_stopping() -> None:
    """Tell systemd the service is about to exit cleanly."""
    notify("STOPPING=1")


def watchdog_ping() -> None:
    """Reset the systemd watchdog timer.  No-op outside systemd."""
    notify("WATCHDOG=1")


__all__ = [
    "announce_ready",
    "announce_stopping",
    "notify",
    "watchdog_ping",
    "watchdog_usec",
]
