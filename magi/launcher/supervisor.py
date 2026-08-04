"""Local Profile process supervisor — the OS-process half of Phase 4.

``LocalProcessRuntimeBackend`` (in :mod:`magi.orchestrator.backends`)
delegates all "talk to the OS" work to this supervisor — spawning
child processes, killing them, watching their PIDs, recovering
after a launcher restart.  The supervisor in turn delegates every
state mutation to the BUS (:class:`ControlRegistryService`), per plan
§4.5 / §7.1: the supervisor never writes directly to the registry.

Layering:

- :mod:`magi.launcher.supervisor` (this file) — OS process primitives
- :mod:`magi.orchestrator.backends.local_process` — backend that
  composes the supervisor + the BUS command surface
- :mod:`magi.bus.services.control_registry` — BUS façade for state
"""

from __future__ import annotations

import errno
import logging
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from magi.bus.db.control.repository import RuntimeStateDTO
from magi.bus.db.control.models import RuntimeObservedState
from magi.bus.services.control_registry import ControlRegistryService
from magi.launcher.paths import (
    control_dir,
    runtime_audit_log_path,
    runtime_log_dir,
    runtime_workspace_root,
)

logger = logging.getLogger("magi.launcher.supervisor")


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """One runtime subprocess to spawn.

    ``argv`` is a list — never ``shell=True`` per plan §7.1.
    """

    runtime_id: int
    slug: str
    argv: list[str]
    env: dict[str, str]
    cwd: Optional[Path] = None


class ProcessHandle:
    """A live OS process handle owned by the supervisor.

    Wraps :class:`subprocess.Popen`; the supervisor monitors the PID
    via :meth:`alive` and terminates via :meth:`terminate`.
    """

    def __init__(self, popen: subprocess.Popen, runtime_id: int) -> None:
        self._popen = popen
        self.runtime_id = runtime_id

    @property
    def pid(self) -> int:
        return self._popen.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._popen.returncode

    def alive(self) -> bool:
        return self._popen.poll() is None

    def terminate(self, *, grace_seconds: float = 5.0) -> None:
        """Send SIGTERM, escalate to SIGKILL after ``grace_seconds``."""
        if not self.alive():
            return
        try:
            if os.name == "posix":
                self._popen.send_signal(signal.SIGTERM)
            else:
                self._popen.terminate()
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if not self.alive():
                return
            time.sleep(0.05)
        try:
            if os.name == "posix":
                self._popen.send_signal(signal.SIGKILL)
            else:
                self._popen.kill()
        except ProcessLookupError:
            return


class ProcessSupervisor:
    """OS-process half of the Local Profile.

    One instance per launcher process.  Keeps an in-memory map of
    ``runtime_id -> ProcessHandle`` for fast start/stop while
    persisting desired / observed state through
    :class:`ControlRegistryService`.
    """

    def __init__(self, control: ControlRegistryService) -> None:
        self._control = control
        self._handles: dict[int, ProcessHandle] = {}

    # -- spawn helpers ------------------------------------------------------

    @contextmanager
    def _workspace(self, runtime_id: int, slug: str):
        """Carve out the per-runtime dirs and update the registry."""
        from magi.launcher.paths import default_data_root

        data_root = default_data_root()
        ws = runtime_workspace_root(data_root, runtime_id, slug)
        log_d = runtime_log_dir(data_root, runtime_id, slug)
        audit_p = runtime_audit_log_path(data_root, runtime_id, slug)
        for d in (ws, log_d):
            d.mkdir(parents=True, exist_ok=True)
        try:
            self._control.attach_paths(
                runtime_id,
                workspace_dir=ws,
                log_dir=log_d,
                audit_log_path=audit_p,
                backend_ref=f"local-{slug}",
            )
            yield ws, log_d, audit_p
        finally:
            pass

    def spawn(self, spec: ProcessSpec) -> ProcessHandle:
        """Spawn one runtime subprocess.

        Idempotent: if a live handle already exists for the same
        ``runtime_id`` it is reused and the call is a no-op.
        """
        existing = self._handles.get(spec.runtime_id)
        if existing is not None and existing.alive():
            return existing
        with self._workspace(spec.runtime_id, spec.slug) as (ws, log_d, audit_p):
            log_file = (log_d / "runtime.log").open("a", encoding="utf-8")
            try:
                env = os.environ.copy()
                env.update(spec.env)
                env["MAGI_RUNTIME_ID"] = str(spec.runtime_id)
                env["MAGI_RUNTIME_WORKSPACE"] = str(ws)
                env["MAGI_RUNTIME_LOG_DIR"] = str(log_d)
                env["MAGI_RUNTIME_AUDIT_LOG"] = str(audit_p)
                popen = subprocess.Popen(
                    spec.argv,
                    cwd=str(spec.cwd) if spec.cwd else str(ws),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    shell=False,  # plan §7.1 — argv array, never shell=True
                )
            finally:
                log_file.close()
        handle = ProcessHandle(popen, spec.runtime_id)
        self._handles[spec.runtime_id] = handle
        return handle

    # -- control surface ---------------------------------------------------

    def stop(self, runtime_id: int, *, grace_seconds: float = 5.0) -> None:
        handle = self._handles.pop(runtime_id, None)
        if handle is None:
            return
        handle.terminate(grace_seconds=grace_seconds)

    def alive(self, runtime_id: int) -> bool:
        handle = self._handles.get(runtime_id)
        if handle is None:
            try:
                row = self._control.get_runtime(runtime_id)
                return row.observed_state in {RuntimeObservedState.STARTED, RuntimeObservedState.STARTING}
            except Exception:
                return False
        return handle.alive()

    def reconcile(self) -> list[int]:
        """Stale-PID recovery for plan §7.1.

        Walks the control registry, checks ``pid`` against
        :func:`pid_alive` (proc/0 PID check), and marks dead rows
        ``stale=True``.  Returns the list of stale runtime_ids.
        """
        stale: list[int] = []
        for row in self._control.list_runtimes():
            if row.observed_state != RuntimeObservedState.STARTED:
                continue
            if row.pid is None:
                self._control.mark_stale(row.runtime_id, stale=True)
                self._control.record_observed(row.runtime_id, RuntimeObservedState.CRASHED)
                stale.append(row.runtime_id)
                continue
            if not pid_alive(row.pid):
                self._control.mark_stale(row.runtime_id, stale=True)
                self._control.record_observed(row.runtime_id, RuntimeObservedState.CRASHED)
                stale.append(row.runtime_id)
        return stale


# -- OS helpers --------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """``True`` iff a process with ``pid`` is currently alive on the host.

    Uses ``kill -0`` on POSIX (catches any signal that requires
    permission).  Windows uses ``OpenProcess`` and falls back to
    ``tasklist``.  This is a best-effort probe — the result is only
    used for stale-PID reconciliation.
    """
    if pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return False
            return True
    # Windows
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
        if not handle:
            return False
        try:
            code = wintypes.DWORD(0)
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return True  # conservative — assume alive when we can't tell


__all__ = [
    "ProcessSpec",
    "ProcessHandle",
    "ProcessSupervisor",
    "pid_alive",
]
