"""Live intranet join: spawned MAGI receives the group invite and joins."""

from __future__ import annotations

import socket
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from magi_asp.asp.spawn import ProcessSpawner
from main import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(probe, *, timeout: float = 12.0):
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        try:
            last = probe()
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
            last = None
        if last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting; last={last!r}")


def test_spawned_magi_joins_group_on_intranet_invite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAGI_SPAWN", "1")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    app = create_app(
        database_path=tmp_path / "asp.sqlite",
        magi_spawner=ProcessSpawner(),
        asp_base=base,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait(lambda: httpx.get(f"{base}/health", timeout=0.5).status_code == 200)

        operator = httpx.get(f"{base}/operator", timeout=2.0).json()
        headers = {"Authorization": f"Bearer {operator['token']}"}
        bot = httpx.post(
            f"{base}/conversations",
            json={"kind": "bot"},
            headers=headers,
            timeout=8.0,
        )
        assert bot.status_code == 201, bot.text
        handle = bot.json()["agents"][0]
        assert bot.json()["name"] == "eva-000"
        assert handle == "@eva-000.magi"
        assert bot.json()["spawned"] is True

        def magi_online():
            rows = httpx.get(f"{base}/bots", headers=headers, timeout=2.0).json()["bots"]
            return any(row["handle"] == handle and row["online"] for row in rows)

        _wait(magi_online)

        renamed = httpx.patch(
            f"{base}/bots/{handle}/nickname",
            json={"nickname": "司空"},
            headers=headers,
            timeout=8.0,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["nickname"] == "司空"
        workspace_db = tmp_path / "home" / ".magi" / "eva-000" / "memories" / "magi.db"
        with sqlite3.connect(workspace_db) as connection:
            nickname = connection.execute(
                "SELECT nickname FROM books_contacts WHERE id = 1"
            ).fetchone()[0]
        assert nickname == "司空"
        listed = httpx.get(f"{base}/bots", headers=headers, timeout=2.0).json()["bots"]
        assert listed[0]["name"] == "司空"

        group = httpx.post(
            f"{base}/conversations",
            json={"kind": "group"},
            headers=headers,
            timeout=4.0,
        ).json()
        group_id = group["conversation_id"]
        added = httpx.post(
            f"{base}/conversations/{group_id}/members",
            json={"handle": handle},
            headers=headers,
            timeout=4.0,
        )
        assert added.status_code == 200, added.text

        def magi_joined():
            view = httpx.get(
                f"{base}/conversations/{group_id}",
                headers=headers,
                timeout=2.0,
            ).json()
            for row in view.get("participants") or []:
                if row.get("handle") == handle and row.get("status") == "joined":
                    return True
            return False

        _wait(magi_joined)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
