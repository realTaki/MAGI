from pathlib import Path

from fastapi.testclient import TestClient

from magi_asp.asp.spawn import RecordingSpawner
from main import create_app


def test_health_creates_the_versioned_local_database(tmp_path: Path) -> None:
    database_path = tmp_path / "asp.sqlite"
    with TestClient(create_app(database_path=database_path)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.app.state.service.database.path == database_path
    assert database_path.exists()


def _client(tmp_path: Path, spawner: RecordingSpawner | None = None) -> TestClient:
    return TestClient(
        create_app(
            database_path=tmp_path / "asp.sqlite",
            magi_spawner=spawner or RecordingSpawner(),
            asp_base="http://asp.test",
        )
    )


def test_operator_bootstrap_is_stable_across_restarts(tmp_path: Path) -> None:
    database_path = tmp_path / "asp.sqlite"
    with TestClient(create_app(database_path=database_path, magi_spawner=RecordingSpawner())) as first:
        operator = first.get("/operator").json()
        assert operator["handle"] == "user"
        assert operator["token"]
    with TestClient(create_app(database_path=database_path, magi_spawner=RecordingSpawner())) as second:
        assert second.get("/operator").json() == operator


def test_new_bot_spawns_magi_and_opens_a_dm(tmp_path: Path) -> None:
    spawner = RecordingSpawner()
    with _client(tmp_path, spawner) as client:
        token = client.get("/operator").json()["token"]
        created = client.post(
            "/conversations",
            json={"kind": "bot"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["kind"] == "bot"
        assert body["conversation_id"]
        assert len(body["agents"]) == 1
        assert body["agents"][0].startswith("@bot-")
        assert body["spawned"] is True
        assert len(spawner.calls) == 1
        assert spawner.calls[0]["handle"] == body["agents"][0]
        assert spawner.calls[0]["base"] == "http://asp.test"
        listed = client.get(
            "/conversations",
            headers={"Authorization": f"Bearer {token}"},
        ).json()["conversations"]
        assert listed[0]["conversation_id"] == body["conversation_id"]
        assert listed[0]["agents"] == body["agents"]


def test_new_group_opens_immediately_without_spawn(tmp_path: Path) -> None:
    spawner = RecordingSpawner()
    with _client(tmp_path, spawner) as client:
        token = client.get("/operator").json()["token"]
        created = client.post(
            "/conversations",
            json={"kind": "group"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["kind"] == "group"
        assert body["agents"] == []
        assert spawner.calls == []
        patched = client.patch(
            f"/conversations/{body['conversation_id']}",
            json={"topic": "offsite", "description": "week of the 14th"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert patched.status_code == 200
        assert patched.json()["topic"] == "offsite"
        assert patched.json()["description"] == "week of the 14th"


def test_create_conversation_rejects_name_and_settings_payloads(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        token = client.get("/operator").json()["token"]
        missing = client.post(
            "/conversations",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert missing.status_code == 422
        extra = client.post(
            "/conversations",
            json={"kind": "bot", "name": "please do not ask", "model": "gpt"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # Extra fields are ignored; create still does not require config.
        assert extra.status_code == 201
        assert extra.json()["kind"] == "bot"


def test_bots_lists_spawned_magi_not_a_static_roster(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        token = client.get("/operator").json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        empty = client.get("/bots", headers=headers)
        assert empty.status_code == 200
        assert empty.json() == {"bots": []}
        first = client.post("/conversations", json={"kind": "bot"}, headers=headers)
        second = client.post("/conversations", json={"kind": "bot"}, headers=headers)
        assert first.status_code == 201
        assert second.status_code == 201
        handle_a = first.json()["agents"][0]
        handle_b = second.json()["agents"][0]
        listed = client.get("/bots", headers=headers).json()["bots"]
        assert {row["handle"] for row in listed} == {handle_a, handle_b}
        assert all(row["online"] is False for row in listed)
        assert "user" not in {row["handle"] for row in listed}
        denied = client.get("/bots")
        assert denied.status_code == 401


def test_group_can_add_a_listed_bot(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        token = client.get("/operator").json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        bot = client.post("/conversations", json={"kind": "bot"}, headers=headers).json()
        group = client.post("/conversations", json={"kind": "group"}, headers=headers).json()
        handle = bot["agents"][0]
        group_id = group["conversation_id"]
        picker = client.get(
            "/bots",
            params={"conversation_id": group_id},
            headers=headers,
        )
        assert picker.status_code == 200
        rows = picker.json()["bots"]
        assert rows == [{"handle": handle, "online": False, "in_conversation": False}]
        added = client.post(
            f"/conversations/{group_id}/members",
            json={"handle": handle},
            headers=headers,
        )
        assert added.status_code == 200
        assert added.json()["agents"] == [handle]
        assert added.json()["kind"] == "group"
        after = client.get(
            "/bots",
            params={"conversation_id": group_id},
            headers=headers,
        ).json()["bots"]
        assert after == [{"handle": handle, "online": False, "in_conversation": True}]
        unknown = client.post(
            f"/conversations/{group_id}/members",
            json={"handle": "@nobody.magi"},
            headers=headers,
        )
        assert unknown.status_code == 404


def _participant_status(view: dict, handle: str) -> str | None:
    for row in view.get("participants") or []:
        if row.get("handle") == handle:
            return row.get("status")
    return None


def _receive_invite(ws, session_id: str, invitee: str) -> dict:
    for _ in range(20):
        event = ws.receive_json()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if (
            event.get("type") == "session.invited"
            and event.get("session_id") == session_id
            and payload.get("invitee") == invitee
        ):
            return event
    raise AssertionError(f"no session.invited for {invitee} in {session_id}")


def test_intranet_invite_marks_the_payload(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        token = client.get("/operator").json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        bot = client.post("/conversations", json={"kind": "bot"}, headers=headers).json()
        events = client.get(
            f"/sessions/{bot['conversation_id']}/events",
            headers=headers,
        ).json()["events"]
        invited = [event for event in events if event["type"] == "session.invited"]
        assert invited
        assert invited[0]["payload"]["intranet"] is True
        assert invited[0]["payload"]["invitee"] == bot["agents"][0]


def test_magi_joins_the_group_when_it_receives_the_invite(tmp_path: Path) -> None:
    """Group profile invite → session.invited → MAGI POST /join. Intranet, no wizard."""
    with _client(tmp_path) as client:
        operator = client.get("/operator").json()["token"]
        headers = {"Authorization": f"Bearer {operator}"}
        bot = client.post("/conversations", json={"kind": "bot"}, headers=headers).json()
        group = client.post("/conversations", json={"kind": "group"}, headers=headers).json()
        handle = bot["agents"][0]
        magi_token = bot["magi"]["token"]
        magi_headers = {"Authorization": f"Bearer {magi_token}"}
        group_id = group["conversation_id"]

        with client.websocket_connect("/connect", headers=magi_headers) as ws:
            online = client.get(
                "/bots",
                params={"conversation_id": group_id},
                headers=headers,
            ).json()["bots"]
            assert online == [
                {"handle": handle, "online": True, "in_conversation": False}
            ]
            added = client.post(
                f"/conversations/{group_id}/members",
                json={"handle": handle},
                headers=headers,
            )
            assert added.status_code == 200
            event = _receive_invite(ws, group_id, handle)
            assert event["payload"]["intranet"] is True
            joined = client.post(f"/sessions/{group_id}/join", headers=magi_headers)
            assert joined.status_code == 200

        view = client.get(f"/conversations/{group_id}", headers=headers).json()
        assert view["agents"] == [handle]
        assert _participant_status(view, handle) == "joined"


def test_offline_magi_joins_when_it_connects_after_the_invite(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        operator = client.get("/operator").json()["token"]
        headers = {"Authorization": f"Bearer {operator}"}
        bot = client.post("/conversations", json={"kind": "bot"}, headers=headers).json()
        group = client.post("/conversations", json={"kind": "group"}, headers=headers).json()
        handle = bot["agents"][0]
        magi_token = bot["magi"]["token"]
        magi_headers = {"Authorization": f"Bearer {magi_token}"}
        group_id = group["conversation_id"]
        added = client.post(
            f"/conversations/{group_id}/members",
            json={"handle": handle},
            headers=headers,
        )
        assert added.status_code == 200
        assert _participant_status(added.json(), handle) == "invited"

        with client.websocket_connect("/connect", headers=magi_headers) as ws:
            event = _receive_invite(ws, group_id, handle)
            assert event["payload"]["intranet"] is True
            joined = client.post(f"/sessions/{group_id}/join", headers=magi_headers)
            assert joined.status_code == 200

        view = client.get(f"/conversations/{group_id}", headers=headers).json()
        assert _participant_status(view, handle) == "joined"
