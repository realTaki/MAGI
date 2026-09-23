"""ASP keeps relay events until every intended recipient confirms them."""

from pathlib import Path
import time

from fastapi.testclient import TestClient

from main import create_app
from server.spawn import RecordingSpawner


def test_relay_survives_restart_and_requires_exact_recipient_acks(tmp_path: Path) -> None:
    database = tmp_path / "asp.sqlite"
    make_app = lambda: create_app(
        database_path=database,
        asp_seed={"@second.magi": "second-token"},
        magi_spawner=RecordingSpawner(),
    )
    with TestClient(make_app()) as client:
        token = client.get("/operator").json()["token"]
        operator = {"Authorization": f"Bearer {token}"}
        second = {"Authorization": "Bearer second-token"}
        conversation = client.post("/conversations", json={"kind": "group"}, headers=operator).json()
        session_id = conversation["conversation_id"]
        assert client.post(
            f"/conversations/{session_id}/members",
            json={"handle": "@second.magi"}, headers=operator,
        ).status_code == 200
        assert client.post(f"/sessions/{session_id}/join", headers=second).status_code == 200
        for content in ("one", "two"):
            assert client.post(
                f"/sessions/{session_id}/messages", json={"content": content}, headers=operator,
            ).status_code == 201
        events = client.get(f"/sessions/{session_id}/events", headers=operator).json()["events"]
        messages = [event for event in events if event["type"] == "session.message"]
        assert [event["payload"]["content"] for event in messages] == ["one", "two"]
        first, later = messages
        for headers in (operator, second):
            assert client.post(
                f"/sessions/{session_id}/events/ack",
                json={"event_ids": [later["event_id"]]}, headers=headers,
            ).status_code == 200
        remaining = client.get(f"/sessions/{session_id}/events", headers=operator).json()["events"]
        assert [event["payload"]["content"] for event in remaining if event["type"] == "session.message"] == ["one"]

    with TestClient(make_app()) as client:
        operator = {"Authorization": f"Bearer {client.get('/operator').json()['token']}"}
        second = {"Authorization": "Bearer second-token"}
        assert client.get("/conversations", headers=operator).json()["conversations"][0]["conversation_id"] == session_id
        assert client.get(f"/sessions/{session_id}/events", headers=operator).json()["events"]
        ack = f"/sessions/{session_id}/events/ack"
        assert client.post(ack, json={"event_ids": [first["event_id"]]}, headers=operator).status_code == 200
        remaining = client.get(f"/sessions/{session_id}/events", headers=operator).json()["events"]
        assert any(event["event_id"] == first["event_id"] for event in remaining)
        assert client.post(ack, json={"event_ids": [first["event_id"]]}, headers=second).status_code == 200
        remaining = client.get(f"/sessions/{session_id}/events", headers=operator).json()["events"]
        assert all(event["type"] != "session.message" for event in remaining)


def test_managed_magi_is_restored_after_asp_restart(tmp_path: Path) -> None:
    database = tmp_path / "asp.sqlite"
    with TestClient(create_app(database_path=database, magi_spawner=RecordingSpawner())) as first:
        token = first.get("/operator").json()["token"]
        response = first.post(
            "/conversations", json={"kind": "bot"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        handle = response.json()["agents"][0]

    spawner = RecordingSpawner()
    with TestClient(create_app(database_path=database, magi_spawner=spawner)):
        deadline = time.monotonic() + 7
        while not spawner.calls and time.monotonic() < deadline:
            time.sleep(0.1)
        assert [call["handle"] for call in spawner.calls] == [handle]
