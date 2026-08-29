"""PID-file process supervision primitives.

Both local supervisors — :mod:`magi.startup.local` (one process per
MAGI) and :mod:`magi.startup.webui` (the single WebUI process) —
follow the same pattern: write a PID file on spawn, then read it back
to answer "is that process still up?". These helpers are that
pattern's whole surface; they lived duplicated (byte-identical) in
both modules until this module claimed them.

Also owns :func:`mark_registry_stopped`, the best-effort helper that
flips the singleton WebUI's view of a MAGI runtime to ``STOPPED``
once its process exits — shared by the detached ``stop_magi`` path
and the foreground ``run_magi`` SIGTERM/SIGINT cleanup hook.

Deliberately narrow: no spawning, no path resolution (that's
:mod:`magi.startup.paths`). Signals are scoped to a single
purpose — the orphan-worker recovery that both supervisors need so
they can recover from a crashed process that leaves a listener orphaned on the
runtime socket.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from magi.startup.config import StartupConfig

if TYPE_CHECKING:
    from magi.startup.config import StartupConfig


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


def find_listener_on_port(port: int) -> int | None:
    """Return the PID of a process listening on ``port`` on loopback, or ``None``.

    Used to recover from a crashed process that keeps the runtime socket open
    while its PID file points at a dead parent. The next ``start_*`` would
    otherwise fail with "Address already in use" because nothing sees the
    orphan.

    Implementation:
      - Linux: parse ``/proc/net/tcp`` (LISTEN state, port in hex),
        then walk ``/proc/<pid>/fd/*`` to find a process holding the
        matching socket inode.  Zero external dependencies.
      - macOS: shell out to ``lsof`` (the only sane option without
        root + kernel tracing).  Returns ``None`` if ``lsof`` is
        missing or times out.
      - Other platforms: ``None`` (the Windows install path uses
        service-manager supervision, not this hot-recovery loop).
    """
    if sys.platform == "linux":
        return _find_listener_on_port_linux(port)
    if sys.platform == "darwin":
        return _find_listener_on_port_macos(port)
    return None


# ----------------------------------------------------------------------
# platform helpers
# ----------------------------------------------------------------------


_LISTEN_STATE = "0A"  # TCP_ESTABLISHED in /proc/net/tcp state column


def _find_listener_on_port_linux(port: int) -> int | None:
    target_hex = f"{port:04X}"

    inodes: set[int] = set()
    try:
        with open("/proc/net/tcp") as f:
            next(f, None)  # skip header
            for line in f:
                parts = line.split()
                if len(parts) < 10:
                    continue
                if parts[3] != _LISTEN_STATE:
                    continue
                if not parts[1].endswith(f":{target_hex}"):
                    continue
                inodes.add(int(parts[9]))
    except (OSError, ValueError):
        return None
    if not inodes:
        return None

    try:
        pid_names = os.listdir("/proc")
    except OSError:
        return None
    for pid_name in pid_names:
        if not pid_name.isdigit():
            continue
        fd_dir = f"/proc/{pid_name}/fd"
        try:
            fd_names = os.listdir(fd_dir)
        except OSError:
            continue
        for fd_name in fd_names:
            try:
                link = os.readlink(f"{fd_dir}/{fd_name}")
            except OSError:
                continue
            if not link.startswith("socket:["):
                continue
            try:
                inode = int(link[len("socket:["):-1])
            except ValueError:
                continue
            if inode in inodes:
                return int(pid_name)
    return None


def _find_listener_on_port_macos(port: int) -> int | None:
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def reap_orphan_listener(port: int, *, label: str = "orphan worker") -> int | None:
    """SIGKILL any orphan still listening on ``port``; return the PID we killed.

    A crashed process can leave a child holding the runtime socket with no PID
    file pointing at it. Without this sweep the next spawn would bind-fail and
    silently strand the slot. SIGKILL is intentional — the orphan is already
    in a bad state and we want the port released *now*, not after a graceful
    drain that the worker may never attempt.

    Returns ``None`` if no orphan was found; otherwise the killed PID.
    Waits up to 2 s for the kernel to actually release the socket,
    since FD teardown can lag the signal.
    """
    orphan = find_listener_on_port(port)
    if orphan is None:
        return None
    print(
        f"{label} pid={orphan} holds port {port}; killing before respawn",
        file=sys.stderr,
    )
    try:
        os.kill(orphan, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if find_listener_on_port(port) is None:
            return orphan
        time.sleep(0.05)
    return orphan


# ----------------------------------------------------------------------
# Registry reconciliation (shared by detached stop + foreground cleanup)
# ----------------------------------------------------------------------


logger = logging.getLogger("magi.startup.process")


def mark_registry_stopped(config: "StartupConfig") -> None:
    """Flip ``runtime_state`` to STOPPED for one MAGI — best-effort.

    Called from :func:`magi.startup.local.stop_magi` after the
    detached subprocess exits, and from the ``run_magi`` SIGTERM/SIGINT
    handler so a foreground-launched runtime also reconciles the
    singleton WebUI's ``/api/auth/available-magi`` view.

    Best-effort by design: if the MAGIS store isn't reachable, the
    registry row is missing, or any of the cross-table lookups
    fail, this is a no-op.  The next ``start_magi`` path reconciles
    via :meth:`set_desired_state` / :meth:`set_observed_state` on
    its own, so a registry-write failure here never strands the slot.
    """
    from magi.startup.paths import resolve_magis_database_url
    from magi.startup.spec import load_runtime_spec
    from magi.old_bus.bases.db.base import utcnow_naive
    from magi.old_bus.firmwares.books.magis.runtimeBook import (
        RuntimeDesiredState,
        RuntimeObservedState,
    )

    try:
        magis_url = config.magis_database_url or resolve_magis_database_url(
            config.host_workspace_dir, config.magis_name
        )
        from magi.old_bus.bootstrap import open_bus

        bus = open_bus(magis_url=magis_url)
        spec = load_runtime_spec(
            bus, config.magi_name, magis_database_url=magis_url
        )
    except Exception:
        logger.warning("mark_registry_stopped: failed to load runtime spec", exc_info=True)
        return
    try:
        magi_id = int(spec.magi_id)
    except (TypeError, ValueError):
        return

    runtimes = bus.runtime_state_book
    if runtimes is None:
        return
    try:
        runtimes.set_desired_state(runtime_id=magi_id, desired_state=RuntimeDesiredState.STOPPED)
        runtimes.set_observed_state(
            runtime_id=magi_id,
            observed_state=RuntimeObservedState.STOPPED,
            stopped_at=utcnow_naive(),
        )
    except Exception:
        # Reconciliation is best-effort; do not mask the caller's
        # exit status on a registry write failure.
        logger.warning("mark_registry_stopped: registry write failed", exc_info=True)


# ----------------------------------------------------------------------
# Foreground lifecycle (shared by run_magi + run_webui_foreground)
# ----------------------------------------------------------------------


def claim_pid_file(pid_path: Path) -> None:
    """Write ``os.getpid()`` into ``pid_path``; refuse if a live run is on record.

    Mirrors the PID-file half of :func:`magi.startup.local.start_magi`
    so the foreground and detached paths are interchangeable from the
    operator's perspective.  A stale PID file pointing at a dead PID
    is overwritten without complaint — same PID-reuse caveat.

    The ``current == existing`` short-circuit handles the detach case:
    :func:`magi.startup.local.start_magi` writes the supervisor's PID
    before spawning the foreground subprocess; that subprocess is the
    foreground process itself, so re-claiming its own PID is not a conflict.
    """
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_pid(pid_path)
    current = os.getpid()
    if existing is not None and is_alive(existing) and existing != current:
        raise RuntimeError(
            f"already running (pid={existing}, pid_file={pid_path})"
        )
    pid_path.write_text(str(current), encoding="utf-8")


@dataclass(slots=True)
class PidCleanup:
    """Best-effort PID-file cleanup fired by SIGTERM/SIGINT.

    Unlinks ``pid_path`` and, optionally, calls ``extra_cleanup`` so
    per-process bookkeeping (e.g. flipping ``runtime_state`` to
    ``STOPPED`` for a MAGI runtime) can share the same signal hook.

    Failures are logged at WARNING and never re-raised — cleanup must
    not block the process from exiting.
    """

    pid_path: Path
    extra_cleanup: Callable[[], None] | None = None
    fired: bool = field(default=False, init=False)

    def run_once(self) -> None:
        """Synchronous entry point used by the ``finally`` in foreground runs."""
        if self.fired:
            return
        self.fired = True
        try:
            self.pid_path.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "pid cleanup: failed to unlink pid file %s",
                self.pid_path,
                exc_info=True,
            )
        if self.extra_cleanup is not None:
            try:
                self.extra_cleanup()
            except Exception:
                logger.warning("pid cleanup: extra_cleanup raised", exc_info=True)

    def handle(self, signum: int, frame) -> None:
        """Signal-handler entry point.  Chains through to the OS default after."""
        self.run_once()
        # Let uvicorn's own SIGTERM/SIGINT handling proceed so the
        # asyncio loop shuts down.  Re-raising the default action is
        # the documented way to chain through from inside a Python
        # signal handler.
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        # SIGTERM: explicitly reference the default handler so uvicorn
        # observes the signal — importing ``signal.default_int_handler``
        # has the import-time side effect of registering it.
        signal.default_int_handler  # noqa: B018


def install_lifecycle_handlers(
    pid_path: Path,
    *,
    extra_cleanup: Callable[[], None] | None = None,
) -> PidCleanup:
    """Install SIGTERM/SIGINT handlers that unlink ``pid_path`` and call ``extra_cleanup``.

    Returns the :class:`PidCleanup` instance so the caller can also
    invoke :meth:`PidCleanup.run_once` from a ``finally`` block when
    uvicorn returns without a signal (e.g. KeyboardInterrupt inside the
    asyncio loop that uvicorn swallows).
    """
    cleanup = PidCleanup(pid_path=pid_path, extra_cleanup=extra_cleanup)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, cleanup.handle)
    return cleanup


__all__ = [
    "read_pid",
    "is_alive",
    "find_listener_on_port",
    "reap_orphan_listener",
    "mark_registry_stopped",
    "claim_pid_file",
    "PidCleanup",
    "install_lifecycle_handlers",
]
