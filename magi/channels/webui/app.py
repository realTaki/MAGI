"""WebUI channel — the HTTP surface Adam (and optionally Eve) serves.

This module builds the FastAPI application that the WebUI channel serves.
It is imported by ``magi.node.run`` only when ``webui`` is in
``MAGI_CHANNELS``.

Mounting order (matters for routing precedence):
  1. ``/health``         — process-level liveness probe.
  2. ``/api/onboarding/*`` — feature routers (verify-bot, save-bot, ...).
  3. ``/``               — SPA static files (built by Vite at web/dist/).
     Uses ``html=True`` so unknown paths fall back to index.html and
     the SPA's client-side router can take over.

Subsequent checkpoints layer on:
- C1.2 — more routers (employees / eves / skills / audit / login).
- C3 — ``/ingest/audit``, ``/ingest/heartbeat`` (EVE → Adam ingest).
- C6 — ``/api/eves/{id}/dispatch``, ``/api/eves/{id}/recall``.
- C7 — WebSocket console stream (``/ws/console``).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from magi import __version__
from magi.channels.webui.api import auth, departments, onboarding

logger = logging.getLogger("magi.channels.webui")

# In-container path the Dockerfile uses. In dev (vite dev), we look
# for the WebUI build output inside the magi/ folder; if not present
# the SPA mount is skipped and vite handles the UI itself on :42069.
_SPA_DIST_CANDIDATES: tuple[Path, ...] = (
    Path("/app/magi/WebUI/dist"),  # Dockerfile runtime stage
    Path(__file__).resolve().parents[2] / "WebUI" / "dist",  # dev checkout (magi/ is parents[2])
)


def _find_spa_dist() -> Path | None:
    for candidate in _SPA_DIST_CANDIDATES:
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    return None


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
    # D.7: lifespan hook starts the auto-title background
    # worker. Kept lazy (inside ``create_app``) so it runs
    # after ``init_orm`` / ``init_sqlite`` have prepared the
    # state — module-level startup would race those calls.
    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # Imported lazily because ``magi.agent.auto_title``
        # also imports ``magi.agent.llm``, which is heavy and
        # not needed for /health. Keeps cold-start tight.
        from magi.agent.memory.session.auto_title import (
            start_title_worker,
            stop_title_worker,
        )

        await start_title_worker()
        logger.info("auto-title worker started")
        try:
            yield
        finally:
            await stop_title_worker()
            logger.info("auto-title worker stopped")

    app = FastAPI(
        title="MAGI",
        version=__version__,
        summary="MAGI node — channel-driven (WebUI / Telegram / …).",
        lifespan=_lifespan,
    )

    # Install the i18n-ready error envelope BEFORE the
    # routers mount so :class:`MagiHTTPException` raised
    # anywhere in the app gets serialised as
    # ``{"code": ..., "detail": ...}``.
    from magi.channels.webui.api.errors import install_error_handler
    install_error_handler(app)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="magi", version=__version__)

    # Feature routers — registered BEFORE the SPA static mount so
    # /api/* always wins over any same-prefixed asset in the SPA bundle.
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(onboarding.router, prefix="/api/onboarding")
    # Departments + Employees routers share the same auth gate
    # (``admin_gate``) but ship as two APIRouters so the prefix
    # stays clean: /api/departments, /api/employees.
    app.include_router(departments.router, prefix="/api")
    app.include_router(departments.employees_router, prefix="/api")
    # Telegram binding (chat_id ↔ employee_id, v0 admin endpoint;
    # C2 will replace with a /start <code> flow that uses the
    # same underlying meta key).
    from magi.channels.webui.api import tg_bindings
    app.include_router(tg_bindings.router, prefix="/api")
    # Adam → system LLM chat (operator types into the WebUI,
    # gets a synchronous reply). v0 non-streaming; C7 swaps
    # in SSE / WebSocket.
    from magi.channels.webui.api import chat
    app.include_router(chat.router, prefix="/api")
    # Chat session CRUD — file-backed per-user conversation
    # history (D.6). Each operator's sessions live under
    # ``<workspace>/memories/sessions/<chat_id>/`` and the
    # cookie pins the operator. Mounted right after ``chat``
    # so its URL prefix aligns with the chat namespace.
    from magi.channels.webui.api import chat_sessions
    app.include_router(chat_sessions.router, prefix="/api")
    # D.18 — full-text search across sessions. Same chat_id
    # scope as ``chat_sessions``; the cookie-derived chat_id
    # is enforced in the SQL join.
    from magi.channels.webui.api import chat_search
    app.include_router(chat_search.router, prefix="/api")
    # Action Items — the "things to do" inbox the dashboard's
    # Action Items sidebar entry fetches. Hooked last so the
    # auth-gated routers above (which it re-imports ``AdminGate``
    # from) are mounted first.
    from magi.channels.webui.api import action_items, contacts
    app.include_router(action_items.router, prefix="/api")
    app.include_router(contacts.router, prefix="/api")
    # Soul editor — the persona text the agent loop reads as
    # the system prompt. Read/write/reset the workspace
    # ``SOUL.md`` from the Settings tab.
    from magi.channels.webui.api import soul
    app.include_router(soul.router, prefix="/api")
    # Telegram channel settings — read-reaction emoji
    # (and future per-channel toggles) edited from the
    # Settings tab. The TG bot reads these on every
    # inbound message so a Save here takes effect
    # immediately, no restart.
    from magi.channels.webui.api import tg_settings
    app.include_router(tg_settings.router, prefix="/api")
    # System settings — per-MAGI config (timezone today;
    # future defaults). The token-bill aggregation endpoint
    # reads the timezone on every call so a Save here is
    # immediately reflected in the next ``GET
    # /api/employees/{id}/token-usage``.
    from magi.channels.webui.api import system_settings
    app.include_router(system_settings.router, prefix="/api")
    # Employee metrics — token-usage aggregation. One
    # endpoint per employee, three periods (week / month /
    # total) in one response.
    from magi.channels.webui.api import employee_metrics
    app.include_router(employee_metrics.router, prefix="/api")
    # Scheduled tasks — operator-facing CRUD + manual
    # trigger. Routed at /api/tasks/*; the LLM-side
    # ``schedule_task`` tool bypasses this router and
    # talks to the registry directly.
    from magi.channels.webui.api import tasks
    app.include_router(tasks.router, prefix="/api")
    # Tools — read-only list of every tool the LLM can call
    # (built-ins + MCP-loaded). The Knowledge tab uses it to
    # render an operator-facing "what can my MAGI do?" view.
    from magi.channels.webui.api import tools
    app.include_router(tools.router, prefix="/api")
    # Skills — read-only catalog of SKILL.md files in
    # workspace/skills/. Knowledge → Skills is the operator-
    # facing surface; the LLM-side equivalent is the
    # ``load_skill`` tool (``magi.agent.skills.loader_tool``).
    from magi.channels.webui.api import skills
    app.include_router(skills.router, prefix="/api")

    # SPA. In Docker this is /app/magi/WebUI/dist (baked in by the web-builder
    # stage). In a local dev checkout with `npm run build` it also gets
    # picked up; if neither produced a dist the mount is skipped and
    # vite dev (on the same :42069) serves the UI itself.
    spa_dist = _find_spa_dist()
    if spa_dist is not None:
        app.mount(
            "/",
            StaticFiles(directory=str(spa_dist), html=True),
            name="spa",
        )
        logger.info("SPA mounted", extra={"path": str(spa_dist)})
    else:
        logger.info(
            "SPA dist not found; webui channel serves API only "
            "(run `npm run build` in magi/WebUI/ or use vite dev to serve the UI)"
        )

    return app


# Module-level instance for ``uvicorn magi.channels.webui.app:app``.
app = create_app()