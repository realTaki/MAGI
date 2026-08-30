from fastapi.testclient import TestClient

from webapp import create_app


def test_health() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}
