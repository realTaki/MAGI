"""HTTP applications for MAGI node APIs.

``create_runtime_app`` is the per-MAGI backend. ``create_control_app`` is
MAGIS-level control. Neither serves the operator UI.

Mounting order (matters for routing precedence):
  1. ``/health``         — process-level liveness probe.
  2. ``/api/*``          — this MAGI node's HTTP API. The operator UI lives
     in the sibling ``app/`` project and is not served from here.

Subsequent checkpoints layer on:
- C1.2 — more routers (contacts / evas / skills / audit / login).
- C3 — ``/ingest/audit``, ``/ingest/heartbeat`` (EVA → ADAM ingest).
- C6 — ``/api/evas/{id}/dispatch``, ``/api/evas/{id}/recall``.
- C7 — WebSocket console stream (``/ws/console``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from pydantic import BaseModel

from channels.api import auth, contacts, magi, magis
from startup import __version__

if TYPE_CHECKING:
    from old_bus import Bus
    from channels.api.control_context import ControlContext
    from startup.workers import WorkerRegistry


class HealthResponse(BaseModel):
    """Liveness payload for ``GET /health``.

    Kept intentionally small — richer status (DB pool, EVA heartbeats,
    audit outbox lag) is added in C8 alongside the hardened degraded-mode
    story.
    """

    status: str
    service: str
    version: str


def create_app(
    *,
    bus: Bus,
    workers: WorkerRegistry | None = None,
    include_control_routes: bool = True,
    include_private_routes: bool = True,
) -> FastAPI:
    """Build this MAGI node's HTTP API.

    ``include_control_routes=False`` is used by every MAGI runtime: it omits
    MAGIS registry routes. The operator UI is not served from this process.

    TelegramWorker lifecycle is owned by the injected RuntimeContext; this
    application only serves HTTP with that already-created context.
    """
    # The MCP bootstrap is handled by the composition root in
    # ``magi.__main__``; the composition root owns the cross-package wiring.
    _ = include_private_routes  # keep the parameter's historical gate

    # Workers are owned by ``startup.runtime``.  The HTTP application
    # only exposes the BUS instance it was explicitly given; it never creates
    # another BUS or worker pool in its lifespan.
    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        yield

    app = FastAPI(
        title="MAGI",
        version=__version__,
        summary="MAGI node HTTP API.",
        lifespan=_lifespan,
    )
    app.state.bus = bus
    app.state.workers = workers

    # Install the i18n-ready error envelope BEFORE the
    # routers mount so :class:`MagiHTTPException` raised
    # anywhere in the app gets serialised as
    # ``{"code": ..., "detail": ...}``.
    from channels.api.errors import install_error_handler

    install_error_handler(app)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="magi", version=__version__)

    # Channel Worker health endpoint
    from channels.api import health as health_api

    app.include_router(health_api.router)

    # Feature routers.
    # /api/* always wins over any same-prefixed asset in the SPA bundle.
    if include_control_routes:
        app.include_router(auth.router, prefix="/api/auth")
        # MAGI management is control-plane state (membership identity plus
        # runtime registry), not a node-local API.  Its self-settings routes
        # are deliberately mounted below on private runtimes only.
        app.include_router(magi.router, prefix="/api")
        app.include_router(magis.router, prefix="/api")
    from channels.api import runtime_control

    app.include_router(runtime_control.router, prefix="/api")
    # Per-MAGI runtime-settings edit surface (provider / API key /
    # model).  Lives next to ``runtime_control`` because both are
    # platform-internal endpoints; ``runtime_proxy`` is what lets
    # the WebUI admin reach this on a non-session MAGI's runtime.
    from channels.api import runtime_provider

    app.include_router(runtime_provider.router, prefix="/api")
    if not include_private_routes:
        from channels.api import runtime_proxy

        app.include_router(runtime_proxy.router, prefix="/api")
        return app
    # Target-scoped login is owned by the MAGI runtime.  The singleton WebUI
    # calls it with a target-bound internal signature before a browser session
    # exists, so it must not be mounted on the browser-facing control service.
    from channels.api import runtime_access

    app.include_router(runtime_access.router, prefix="/api")
    app.include_router(magi.self_router, prefix="/api")
    # Organisation routes execute inside the selected MAGI runtime as well.
    if not include_control_routes:
        app.include_router(magis.router, prefix="/api")
    # Contacts router — unified contact directory + CRUD.
    # Serves both the Knowledge pane (GET ?with_notes=true)
    # and the admin management surface (POST/PATCH).
    app.include_router(contacts.router, prefix="/api")
    # MAGIS router — the "MAGI Societies" surface for the group tree.
    if include_control_routes:
        from channels.api import runtime_proxy

        app.include_router(runtime_proxy.router, prefix="/api")
    # Telegram binding (chat id ↔ contact_id, v0 admin endpoint;
    # C2 will replace with a /start <code> flow that uses the
    # same underlying meta key).
    from channels.api import tg_bindings

    app.include_router(tg_bindings.router, prefix="/api")
    # ADAM → system LLM chat (operator types into the WebUI,
    # gets a synchronous reply). v0 non-streaming; C7 swaps
    # in SSE / WebSocket.
    from channels.api import chat

    app.include_router(chat.router, prefix="/api")
    # Chat conversation CRUD — per-user conversation
    # history (D.6). Each operator's conversations live under
    # ``<workspace>/memories/<state>.db`` (D.18) and the
    # cookie pins the operator. Mounted right after ``chat``
    # so its URL prefix aligns with the chat namespace.
    from channels.api import chat_conversations

    app.include_router(chat_conversations.router, prefix="/api")
    # D.18 — full-text search across conversations. Same contact_id
    # scope as ``chat_conversations``; the cookie-derived contact_id
    # is enforced in the SQL join.
    from channels.api import chat_search

    app.include_router(chat_search.router, prefix="/api")
    # Action Items — the "things to do" inbox the dashboard's
    # Action Items sidebar entry fetches. Hooked last so the
    # auth-gated routers above (which it re-imports ``AdminGate``
    # from) are mounted first.
    from channels.api import action_items, memory

    app.include_router(action_items.router, prefix="/api")
    app.include_router(memory.router, prefix="/api")
    # Soul editor — the persona text the agent loop reads as
    # the system prompt. Read/write/reset the workspace
    # managed Agent persona from the Settings tab.
    from channels.api import soul

    app.include_router(soul.router, prefix="/api")
    # Telegram channel settings — read-reaction emoji
    # (and future per-channel toggles) edited from the
    # Settings tab. The TG bot reads these on every
    # inbound message so a Save here takes effect
    # immediately, no restart.
    from channels.api import tg_settings

    app.include_router(tg_settings.router, prefix="/api")
    # Channel management — list enabled channels, toggle them
    # on/off at runtime. Replaces the MAGI_CHANNELS env var.
    from channels.api import channels as channels_api

    app.include_router(channels_api.router, prefix="/api")
    # System settings — per-MAGI config (timezone today;
    # future defaults). The token-bill aggregation endpoint
    # reads the timezone on every call so a Save here is
    # immediately reflected in the next ``GET
    # ``/api/contacts/…/token-usage``.
    from channels.api import system_settings

    app.include_router(system_settings.router, prefix="/api")
    # Contact token metrics — token-usage aggregation. One
    # endpoint per contact, three periods (week / month /
    # total) in one response.
    from channels.api import token_metrics

    app.include_router(token_metrics.router, prefix="/api")
    # Scheduled tasks — operator-facing CRUD + manual
    # trigger. Routed at /api/tasks/*; the LLM-side
    # ``schedule_task`` tool bypasses this router and
    # talks to the registry directly.
    from channels.api import tasks

    app.include_router(tasks.router, prefix="/api")
    # Tools — read-only list of every tool the LLM can call
    # (built-ins + MCP-loaded). The Knowledge tab uses it to
    # render an operator-facing "what can my MAGI do?" view.
    from channels.api import tools

    app.include_router(tools.router, prefix="/api")
    # MCP servers — operator-facing CRUD for the
    # ``mcp_servers`` table. The DB is the single source
    # of truth for which MCP servers the agent loop
    # connects to (replaces the legacy ``mcp.json`` flow).
    # Settings → MCP card drives this router; the
    # Knowledge → MCP tab stays read-only and surfaces
    # the tool list as the loader caches it.
    from channels.api import mcp_servers

    app.include_router(mcp_servers.router, prefix="/api")
    # Skills — read-only catalog of SKILL.md files in
    # workspace/skills/. Knowledge → Skills is the operator-
    # facing surface; the LLM-side equivalent is the
    # ``load_skill`` tool (``tools.skills.load_skill``).
    from channels.api import skills

    app.include_router(skills.router, prefix="/api")
    return app


def create_runtime_app(*, bus: "Bus", workers: "WorkerRegistry") -> FastAPI:
    """Factory for the internal API served by every MAGI runtime.

    Both arguments are explicit: this module never reaches back into
    ``startup`` to construct them — the composition root injects
    the already-built ``RuntimeContext`` here as its constituent parts.
    """
    return create_app(
        bus=bus,
        workers=workers,
        include_control_routes=False,
    )


def create_control_app(*, context: ControlContext) -> FastAPI:
    """Factory for MAGIS-level control APIs; it has no local MAGI state and no UI."""
    return create_app(
        bus=context.bus, include_control_routes=True, include_private_routes=False
    )
