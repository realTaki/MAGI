from pathlib import Path

from fastapi.testclient import TestClient

from magi_asp.main import create_app


def test_health_creates_the_versioned_local_database(tmp_path: Path) -> None:
    database_path = tmp_path / "asp.sqlite"
    with TestClient(create_app(database_path=database_path)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.app.state.service.database.path == database_path
    assert database_path.exists()
