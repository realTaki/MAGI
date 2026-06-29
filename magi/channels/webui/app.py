"""WebUI channel — the Adam-facing HTTP surface (and optionally Eve-facing).

This module builds the FastAPI application that the WebUI channel serves.
It is imported by ``magi.node.run`` only when ``webui`` is in
``MAGI_CHANNELS``. Subsequent checkpoints layer on:

- C1 — HTMX CRUD pages (employees / eves / skills / knowledge / audit).
- C3 — ``/ingest/audit``, ``/ingest/heartbeat`` (EVE → Adam ingest).
- C6 — ``/api/eves/{id}/dispatch``, ``/api/eves/{id}/recall``.
- C7 — WebSocket console stream (``/ws/console``).

The ``/health`` endpoint is a process-level liveness probe (not a WebUI
feature) — it stays here because FastAPI is the only HTTP server in
this codebase and ``/health`` has to live somewhere.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from magi import __version__


class HealthResponse(BaseModel):
    """Liveness payload for ``GET /health``.

    Kept intentionally small — richer status (DB pool, EVE heartbeats,
    audit outbox lag) is added in C8 alongside the hardened degraded-mode
    story.
    """

    status: str
    service: str
    version: str


def create_app() -> FastAPI:
    app = FastAPI(
        title="MAGI",
        version=__version__,
        summary="MAGI node — channel-driven (WebUI / Telegram / …).",
    )

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="magi", version=__version__)

    return app


# Module-level instance for ``uvicorn magi.channels.webui.app:app``.
app = create_app()