"""Provider settings: ASP stores them and hands them to every MAGI."""

from pathlib import Path

from fastapi.testclient import TestClient
from main import create_app
from server.spawn import RecordingSpawner


def operator_headers(client: TestClient) -> dict[str, str]:
    operator = client.get("/operator").json()
    return {"Authorization": f"Bearer {operator['token']}"}


def test_provider_settings_round_trip(tmp_path: Path) -> None:
    database_path = tmp_path / "asp.sqlite"
    with TestClient(
        create_app(database_path=database_path, magi_spawner=RecordingSpawner())
    ) as client:
        assert client.get("/settings/provider").status_code == 401
        headers = operator_headers(client)
        assert client.get("/settings/provider", headers=headers).json() == {
            "provider": None,
            "model": None,
            "api_key": None,
        }

        saved = client.put(
            "/settings/provider",
            headers=headers,
            json={"provider": "claude", "model": "claude-opus-5", "api_key": "sk-test"},
        ).json()
        assert saved["provider"] == "claude"
        assert saved["model"] == "claude-opus-5"
        assert saved["api_key"] == "sk-test"
        # No MAGI is connected in this test, so there is nobody to hand it to.
        assert saved["synced"] == []
        assert saved["failed"] == []

    # A restart keeps the settings: they live in the ASP database.
    with TestClient(
        create_app(database_path=database_path, magi_spawner=RecordingSpawner())
    ) as client:
        stored = client.get("/settings/provider", headers=operator_headers(client)).json()
        assert stored["provider"] == "claude"
        assert stored["model"] == "claude-opus-5"
        assert stored["api_key"] == "sk-test"


def test_provider_settings_partial_update_and_clearing(tmp_path: Path) -> None:
    database_path = tmp_path / "asp.sqlite"
    with TestClient(
        create_app(database_path=database_path, magi_spawner=RecordingSpawner())
    ) as client:
        headers = operator_headers(client)
        client.put(
            "/settings/provider",
            headers=headers,
            json={"provider": "openai", "model": "gpt-5.6", "api_key": "sk-openai"},
        )

        # An omitted field keeps its value; an empty string clears it.
        client.put("/settings/provider", headers=headers, json={"model": "gpt-5.6-sol"})
        client.put("/settings/provider", headers=headers, json={"api_key": ""})

        assert client.get("/settings/provider", headers=headers).json() == {
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "api_key": None,
        }
