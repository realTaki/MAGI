"""Tests for :mod:`startup.systemd_notify` — sd_notify wrapper."""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

from startup import systemd_notify


@pytest.fixture
def notify_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Bind a real datagram UNIX socket and point ``$NOTIFY_SOCKET`` at it.

    Yields ``(sock_path, server_socket)`` — tests drain datagrams off
    the bound server socket itself; AF_UNIX datagrams addressed to
    the bound path land in its receive queue.
    """
    sock_path = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(sock_path)
    server.settimeout(2.0)
    monkeypatch.setattr(systemd_notify, "_notify_socket_path", None)
    monkeypatch.setenv("NOTIFY_SOCKET", sock_path)
    yield sock_path, server
    try:
        server.close()
    finally:
        os.unlink(sock_path)


def _drain(sock: socket.socket, expected: str, timeout: float = 2.0) -> str:
    """Read datagrams until ``expected`` shows up or ``timeout`` expires."""
    deadline = time.monotonic() + timeout
    received = ""
    while time.monotonic() < deadline:
        try:
            chunk, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        received += chunk.decode("utf-8")
        if expected in received:
            return received
    raise AssertionError(f"timeout waiting for {expected!r}; got {received!r}")


def test_notify_sends_to_socket(notify_socket) -> None:
    """Frame is delivered verbatim over the socket."""
    sock_path, server = notify_socket
    assert systemd_notify.notify("READY=1") is True
    received = _drain(server, "READY=1")
    assert received.strip() == "READY=1"


def test_notify_returns_false_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.setattr(systemd_notify, "_notify_socket_path", None)
    assert systemd_notify.notify("READY=1") is False


def test_watchdog_usec_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")  # 30s
    assert systemd_notify.watchdog_usec() == 30_000_000


def test_watchdog_usec_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert systemd_notify.watchdog_usec() is None


def test_watchdog_ping_noop_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    monkeypatch.setattr(systemd_notify, "_notify_socket_path", None)
    # Must not raise even when no socket exists.
    systemd_notify.watchdog_ping()


def test_announce_ready_is_a_notify_call(notify_socket) -> None:
    sock_path, server = notify_socket
    systemd_notify.announce_ready()
    assert "READY=1" in _drain(server, "READY=1")


def test_announce_stopping_is_a_notify_call(notify_socket) -> None:
    sock_path, server = notify_socket
    systemd_notify.announce_stopping()
    assert "STOPPING=1" in _drain(server, "STOPPING=1")


__all__ = []