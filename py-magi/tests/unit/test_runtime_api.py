"""Public API boundary for a single MAGI runtime."""

from __future__ import annotations

from fastapi.testclient import TestClient

from bus import Bus, ChatNotify, JobStatus
from magi.api.app import create_runtime_app


def test_runtime_exposes_only_health_and_conversation_writes(tmp_path) -> None:
    with Bus(tmp_path) as bus:
        app = create_runtime_app(bus=bus)
        client = TestClient(app)

        paths = set(app.openapi()["paths"])
        assert paths == {
            "/health",
            "/api/conversations",
            "/api/conversations/{conversation_id}/messages",
        }

        assert client.get("/health").json() == {"status": "ok"}

        created = client.post("/api/conversations")
        assert created.status_code == 201
        conversation_id = created.json()["conversation_id"]

        sent = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"text": "hello MAGI"},
        )
        assert sent.status_code == 202
        job_id = sent.json()["job_id"]
        board = bus.board(ChatNotify)
        assert board is not None
        assert board.check_job_status(job_id) in {JobStatus.PREPARING, JobStatus.PENDING}

        assert client.get("/api/tasks").status_code == 404
        assert client.get("/api/contacts").status_code == 404
        assert client.get("/api/skills").status_code == 404
