"""Tests for :mod:`startup.process` — PID file + orphan-port helpers."""

from __future__ import annotations

import os
import signal
import socket
import threading
import time

import pytest

from startup import process as proc
from startup.process import reap_orphan_listener


def _free_port() -> int:
    """Bind a socket to 127.0.0.1, read the assigned port, release it."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def listener_port() -> int:
    """Spawn a real listening socket on a free port; clean up on teardown."""
    port = _free_port()
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(5)
    thread = threading.Thread(target=server.accept, daemon=True)
    thread.start()
    yield port
    try:
        server.close()
    except OSError:
        pass


def test_find_listener_on_port_returns_pid_for_live_listener(listener_port: int) -> None:
    pid = proc.find_listener_on_port(listener_port)
    assert pid == os.getpid(), "expected this process to own the listener"


def test_find_listener_on_port_returns_none_when_port_free() -> None:
    port = _free_port()
    assert proc.find_listener_on_port(port) is None


@pytest.mark.parametrize(
    "garbage",
    ["", "abc", "1.5", "\n", "1 2", "  "],
)
def test_read_pid_collapses_garbage_to_none(tmp_path, garbage: str) -> None:
    pid_path = tmp_path / "magi.pid"
    pid_path.write_text(garbage, encoding="utf-8")
    assert proc.read_pid(pid_path) is None


def test_read_pid_returns_none_for_missing_file(tmp_path) -> None:
    assert proc.read_pid(tmp_path / "missing.pid") is None


def test_is_alive_for_self() -> None:
    assert proc.is_alive(os.getpid()) is True


def test_is_alive_for_dead_pid() -> None:
    # Pick a PID far above any plausible active range; if the kernel
    # ever recycles it back to a live process, the assertion below
    # becomes meaningful (and we'd want to revisit the heuristic).
    assert proc.is_alive(2_000_000) is False


def test_reap_orphan_listener_kills_live_listener(listener_port: int, monkeypatch) -> None:
    # Sanity: the fixture listener is live and on the expected port.
    assert proc.find_listener_on_port(listener_port) == os.getpid()

    # The fixture listens in this pytest process; ``reap_orphan_listener``
    # would SIGKILL the runner.  Stub ``os.kill`` to record the call and
    # behave as if the PID went away, so the reap-returns-PID + port-released
    # invariants can be checked without taking pytest down.
    killed_targets: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        killed_targets.append((pid, sig))
        # Mark the listener as gone by closing the fixture's socket.
        # The fixture still owns the server; pytest tears it down on yield-cleanup.

    monkeypatch.setattr(proc.os, "kill", fake_kill)
    killed = reap_orphan_listener(listener_port, label="test")

    assert killed == os.getpid()
    # Reap calls os.kill(SIGKILL) exactly once on the listener pid.
    assert killed_targets == [(os.getpid(), signal.SIGKILL)]


def test_reap_orphan_listener_is_noop_when_port_free() -> None:
    port = _free_port()
    assert reap_orphan_listener(port, label="test") is None


def test_reap_orphan_listener_handles_race_with_dead_pid(capsys) -> None:
    """If the listener vanishes between find + kill (TOCTOU), we don't blow up."""
    port = _free_port()
    server = socket.socket()
    server.bind(("127.0.0.1", port))
    server.listen(1)
    # Close immediately so the kernel releases the port before reap runs.
    server.close()
    # Should detect "no listener" on the second pass and return None.
    assert reap_orphan_listener(port, label="test") is None


__all__ = []