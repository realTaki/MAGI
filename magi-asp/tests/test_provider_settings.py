"""ASP forwards provider settings without owning the API key."""

from pathlib import Path

from db.database import LocalDatabase
from fastapi.testclient import TestClient
from main import create_app
from server.spawn import RecordingSpawner


def operator_headers(client: TestClient) -> dict[str, str]:
    operator = client.get("/operator").json()
    return {"Authorization": f"Bearer {operator['token']}"}


def test_provider_update_is_transient(tmp_path: Path) -> None:
    database_path = tmp_path / "asp.sqlite"
    with TestClient(
        create_app(database_path=database_path, magi_spawner=RecordingSpawner())
    ) as client:
        headers = operator_headers(client)
        assert client.put("/settings/provider", json={"api_key": "secret"}).status_code == 401
        saved = client.put(
            "/settings/provider",
            headers=headers,
            json={"provider": "claude", "model": "claude-opus-5", "api_key": "sk-test"},
        ).json()
        assert saved == {
            "provider": "claude",
            "model": "claude-opus-5",
            "api_key": "sk-test",
            "synced": [],
            "failed": [],
        }
        assert client.get("/settings/provider/legacy", headers=headers).json() is None

    database = LocalDatabase(database_path)
    database.open()
    try:
        assert database.get_setting("provider") is None
    finally:
        database.close()


def test_app_can_migrate_and_delete_legacy_provider(tmp_path: Path) -> None:
    database_path = tmp_path / "asp.sqlite"
    database = LocalDatabase(database_path)
    database.open()
    database.set_setting("provider", {"provider": "openai", "api_key": "old-key"})
    database.close()

    with TestClient(
        create_app(database_path=database_path, magi_spawner=RecordingSpawner())
    ) as client:
        headers = operator_headers(client)
        assert client.get("/settings/provider/legacy").status_code == 401
        assert client.get("/settings/provider/legacy", headers=headers).json() == {
            "provider": "openai", "api_key": "old-key"
        }
        assert client.delete("/settings/provider/legacy", headers=headers).json() == {"ok": True}
        assert client.get("/settings/provider/legacy", headers=headers).json() is None
