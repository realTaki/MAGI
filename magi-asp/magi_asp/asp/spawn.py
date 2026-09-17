"""Start a MAGI process attached to this ASP.

Tests inject a stub so conversation-create does not boot a runtime.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SpawnedMagi:
    handle: str
    token: str
    pid: int | None
    spawned: bool


class MagiSpawner(Protocol):
    def spawn(self, *, handle: str, base: str, token: str) -> SpawnedMagi: ...

    def close(self) -> None: ...


@dataclass
class RecordingSpawner:
    """Test double: records spawn calls and does not start a process."""

    calls: list[dict[str, str]] = field(default_factory=list)

    def spawn(self, *, handle: str, base: str, token: str) -> SpawnedMagi:
        self.calls.append({"handle": handle, "base": base, "token": token})
        return SpawnedMagi(handle=handle, token=token, pid=0, spawned=True)

    def close(self) -> None:
        return None


class ProcessSpawner:
    """ASP-side spawn of ``python -m magi``. Desktop clients must not call this."""

    def __init__(self) -> None:
        self._children: list[subprocess.Popen[bytes]] = []

    def spawn(self, *, handle: str, base: str, token: str) -> SpawnedMagi:
        if _spawn_disabled():
            return SpawnedMagi(handle=handle, token=token, pid=None, spawned=False)
        python = _resolve_magi_python()
        cwd = _py_magi_dir()
        try:
            child = subprocess.Popen(
                magi_cli(python, handle, base, token),
                cwd=str(cwd) if cwd is not None else None,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return SpawnedMagi(handle=handle, token=token, pid=None, spawned=False)
        self._children.append(child)
        return SpawnedMagi(handle=handle, token=token, pid=child.pid, spawned=True)

    def close(self) -> None:
        for child in self._children:
            if child.poll() is None:
                child.terminate()
        self._children.clear()


def magi_cli(python: str, handle: str, base: str, token: str) -> list[str]:
    """One MAGI: ``python -m magi <handle> <base> <token>``."""
    return [python, "-m", "magi", handle, base, token]


def in_kubernetes() -> bool:
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def default_spawner() -> MagiSpawner:
    if in_kubernetes():
        from .k8s import KubernetesSpawner

        return KubernetesSpawner()
    return ProcessSpawner()


def _spawn_disabled() -> bool:
    flag = os.environ.get("MAGI_SPAWN", "1").strip().lower()
    return flag in {"0", "false", "no", "off"}


def _resolve_magi_python() -> str:
    env = os.environ.get("MAGI_PYTHON")
    if env:
        return env
    py_magi = _py_magi_dir()
    if py_magi is not None:
        unix = py_magi / ".venv" / "bin" / "python"
        win = py_magi / ".venv" / "Scripts" / "python.exe"
        if unix.exists():
            return str(unix)
        if win.exists():
            return str(win)
    return sys.executable


def _py_magi_dir() -> Path | None:
    # magi-asp/magi_asp/asp/spawn.py → repo root is parents[3]
    repo = Path(__file__).resolve().parents[3]
    candidate = repo / "py-magi"
    return candidate if candidate.is_dir() else None


def spawn_to_wire(spawned: SpawnedMagi) -> Mapping[str, object]:
    return {
        "handle": spawned.handle,
        "pid": spawned.pid,
        "spawned": spawned.spawned,
    }
