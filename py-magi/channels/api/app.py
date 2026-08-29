"""HTTP applications for the unified WebUI and private MAGI Runtime API.

``create_app`` builds the singleton browser-facing control service. Runtime
containers use ``create_runtime_app`` instead: it has no SPA mount and omits
control registry/login routes, while retaining local APIs reached through the
authenticated WebUI proxy.

Mounting order (matters for routing precedence):
  1. ``/health``         — process-level liveness probe.
  2. ``/``               — SPA static files (built by Vite at /app/dist).
     Uses ``html=True`` so unknown paths fall back to index.html and
     the SPA's client-side router can take over.

Subsequent checkpoints layer on:
- C1.2 — more routers (contacts / evas / skills / audit / login).
- C3 — ``/ingest/audit``, ``/ingest/heartbeat`` (EVA → ADAM ingest).
- C6 — ``/api/evas/{id}/dispatch``, ``/api/evas/{id}/recall``.
- C7 — WebSocket console stream (``/ws/console``).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse

from magi import __version__
from magi.channels.api import auth, contacts, magi, magis

if TYPE_CHECKING:
    from magi.old_bus import Bus
    from magi.channels.api.control_context import ControlContext
    from magi.startup.workers import WorkerRegistry

logger = logging.getLogger("magi.channels.api")


class _SpaFallback(StaticFiles):
    """StaticFiles with a real SPA shell fallback.

    Starlette's ``StaticFiles(html=True)`` only swaps ``index.html`` in
    when a request *for a .html path* misses. It does NOT fallback
    arbitrary client-side routes (``/dashboard``, ``/chat/123``) to
    the SPA shell. Those would otherwise 404 on a hard navigation
    (e.g. a link rendered with ``<a href>`` instead of the SPA router),
    which is exactly what action-item ``target_url``s do today.
    """

    def __init__(
        self,
        *,
        directory: str | os.PathLike[str],
        html: bool = False,
        check_dir: bool = True,
    ) -> None:
        # Starlette's parent ``StaticFiles.__init__`` keeps ``directory``
        # as the raw string and indexes it with ``self.directory / ...``
        # in the hot path. That breaks for ``str`` inputs the moment the
        # SPA fallback looks up ``index.html``; coerce to ``Path`` here
        # so ``get_response`` can do its ``self.directory / "index.html"``
        # without raising ``TypeError``.
        from pathlib import Path

        directory_path = Path(directory) if not isinstance(directory, Path) else directory
        super().__init__(directory=directory_path, html=html, check_dir=check_dir)

    async def get_response(self, path, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            # Real file miss → serve the SPA shell so the client-side
            # router can take over and parse ``?tab=...`` etc.
            # ``self.directory`` is guaranteed non-None by Starlette's
            # ``check_dir`` flow (constructor raises if it's None / missing).
            directory = self.directory
            assert directory is not None, "StaticFiles directory must be set"
            index = Path(directory) / "index.html"
            if index.is_file():
                return FileResponse(str(index), media_type="text/html")
            raise

# In-container and monorepo paths for the sibling ``app`` project. In dev (vite
# dev), no dist exists and Vite handles the UI itself on :42069.
_SPA_DIST_CANDIDATES: tuple[Path, ...] = (
    Path("/app/dist"),  # Dockerfile runtime stage
    Path(__file__).resolve().parents[4] / "app" / "dist",  # repository checkout
)


def _find_spa_dist() -> Path | None:
    for candidate in _SPA_DIST_CANDIDATES:
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    return None


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
    include_spa: bool = True,
    include_control_routes: bool = True,
    include_private_routes: bool = True,
) -> FastAPI:
    """Build either the standalone control WebUI or an internal Runtime API.

    ``include_control_routes=False`` is used by every MAGI runtime: it omits
    login and MAGIS registry routes and never mounts React assets.
    The runtime remains an internal HTTP API for the one WebUI service.

    TelegramWorker lifecycle is owned by the injected RuntimeContext; this
    application only serves HTTP with that already-created context.
    """
    # The MCP bootstrap is handled by the composition root in
    # ``magi.__main__``; the composition root owns the cross-package wiring.
    _ = include_private_routes  # keep the parameter's historical gate

    # Workers are owned by ``magi.startup.runtime``.  The HTTP application
    # only exposes the BUS instance it was explicitly given; it never creates
    # another BUS or worker pool in its lifespan.
    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        yield

    app = FastAPI(
        title="MAGI",
        version=__version__,
        summary="MAGI node — channel-driven (WebUI / Telegram / …).",
        lifespan=_lifespan,
    )
    app.state.bus = bus
    app.state.workers = workers

    # Install the i18n-ready error envelope BEFORE the
    # routers mount so :class:`MagiHTTPException` raised
    # anywhere in the app gets serialised as
    # ``{"code": ..., "detail": ...}``.
    from magi.channels.api.errors import install_error_handler

    install_error_handler(app)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="magi", version=__version__)

    # Channel Worker health endpoint
    from magi.channels.api import health as health_api

    app.include_router(health_api.router)

    # Feature routers — registered BEFORE the SPA static mount so
    # /api/* always wins over any same-prefixed asset in the SPA bundle.
    if include_control_routes:
        app.include_router(auth.router, prefix="/api/auth")
        # MAGI management is control-plane state (membership identity plus
        # runtime registry), not a node-local API.  Its self-settings routes
        # are deliberately mounted below on private runtimes only.
        app.include_router(magi.router, prefix="/api")
        app.include_router(magis.router, prefix="/api")
    from magi.channels.api import runtime_control

    app.include_router(runtime_control.router, prefix="/api")
    # Per-MAGI runtime-settings edit surface (provider / API key /
    # model).  Lives next to ``runtime_control`` because both are
    # platform-internal endpoints; ``runtime_proxy`` is what lets
    # the WebUI admin reach this on a non-session MAGI's runtime.
    from magi.channels.api import runtime_provider

    app.include_router(runtime_provider.router, prefix="/api")
    if not include_private_routes:
        from magi.channels.api import runtime_proxy

        app.include_router(runtime_proxy.router, prefix="/api")
        spa_dist = _find_spa_dist() if include_spa else None
        if spa_dist is not None:
            # See ``_SpaFallback`` docstring — Starlette's
            # ``StaticFiles(html=True)`` does NOT fallback arbitrary
            # client-side routes (``/dashboard``, ``/chat/123``) to the
            # SPA shell; it only swaps ``index.html`` when a .html
            # request misses. Without this catch-all, every
            # ``<a href="/internal-link">`` does a hard nav that 404s.
            app.mount("/", _SpaFallback(directory=str(spa_dist), html=True), name="spa")
        return app
    # Target-scoped login is owned by the MAGI runtime.  The singleton WebUI
    # calls it with a target-bound internal signature before a browser session
    # exists, so it must not be mounted on the browser-facing control service.
    from magi.channels.api import runtime_access

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
        from magi.channels.api import runtime_proxy

        app.include_router(runtime_proxy.router, prefix="/api")
    # Telegram binding (chat id ↔ contact_id, v0 admin endpoint;
    # C2 will replace with a /start <code> flow that uses the
    # same underlying meta key).
    from magi.channels.api import tg_bindings

    app.include_router(tg_bindings.router, prefix="/api")
    # ADAM → system LLM chat (operator types into the WebUI,
    # gets a synchronous reply). v0 non-streaming; C7 swaps
    # in SSE / WebSocket.
    from magi.channels.api import chat

    app.include_router(chat.router, prefix="/api")
    # Chat conversation CRUD — per-user conversation
    # history (D.6). Each operator's conversations live under
    # ``<workspace>/memories/<state>.db`` (D.18) and the
    # cookie pins the operator. Mounted right after ``chat``
    # so its URL prefix aligns with the chat namespace.
    from magi.channels.api import chat_conversations

    app.include_router(chat_conversations.router, prefix="/api")
    # D.18 — full-text search across conversations. Same contact_id
    # scope as ``chat_conversations``; the cookie-derived contact_id
    # is enforced in the SQL join.
    from magi.channels.api import chat_search

    app.include_router(chat_search.router, prefix="/api")
    # Action Items — the "things to do" inbox the dashboard's
    # Action Items sidebar entry fetches. Hooked last so the
    # auth-gated routers above (which it re-imports ``AdminGate``
    # from) are mounted first.
    from magi.channels.api import action_items, memory

    app.include_router(action_items.router, prefix="/api")
    app.include_router(memory.router, prefix="/api")
    # Soul editor — the persona text the agent loop reads as
    # the system prompt. Read/write/reset the workspace
    # managed Agent persona from the Settings tab.
    from magi.channels.api import soul

    app.include_router(soul.router, prefix="/api")
    # Telegram channel settings — read-reaction emoji
    # (and future per-channel toggles) edited from the
    # Settings tab. The TG bot reads these on every
    # inbound message so a Save here takes effect
    # immediately, no restart.
    from magi.channels.api import tg_settings

    app.include_router(tg_settings.router, prefix="/api")
    # Channel management — list enabled channels, toggle them
    # on/off at runtime. Replaces the MAGI_CHANNELS env var.
    from magi.channels.api import channels as channels_api

    app.include_router(channels_api.router, prefix="/api")
    # System settings — per-MAGI config (timezone today;
    # future defaults). The token-bill aggregation endpoint
    # reads the timezone on every call so a Save here is
    # immediately reflected in the next ``GET
    # ``/api/contacts/…/token-usage``.
    from magi.channels.api import system_settings

    app.include_router(system_settings.router, prefix="/api")
    # Contact token metrics — token-usage aggregation. One
    # endpoint per contact, three periods (week / month /
    # total) in one response.
    from magi.channels.api import token_metrics

    app.include_router(token_metrics.router, prefix="/api")
    # Scheduled tasks — operator-facing CRUD + manual
    # trigger. Routed at /api/tasks/*; the LLM-side
    # ``schedule_task`` tool bypasses this router and
    # talks to the registry directly.
    from magi.channels.api import tasks

    app.include_router(tasks.router, prefix="/api")
    # Tools — read-only list of every tool the LLM can call
    # (built-ins + MCP-loaded). The Knowledge tab uses it to
    # render an operator-facing "what can my MAGI do?" view.
    from magi.channels.api import tools

    app.include_router(tools.router, prefix="/api")
    # MCP servers — operator-facing CRUD for the
    # ``mcp_servers`` table. The DB is the single source
    # of truth for which MCP servers the agent loop
    # connects to (replaces the legacy ``mcp.json`` flow).
    # Settings → MCP card drives this router; the
    # Knowledge → MCP tab stays read-only and surfaces
    # the tool list as the loader caches it.
    from magi.channels.api import mcp_servers

    app.include_router(mcp_servers.router, prefix="/api")
    # Skills — read-only catalog of SKILL.md files in
    # workspace/skills/. Knowledge → Skills is the operator-
    # facing surface; the LLM-side equivalent is the
    # ``load_skill`` tool (``magi.tools.skills.load_skill``).
    from magi.channels.api import skills

    app.include_router(skills.router, prefix="/api")

    # SPA. In Docker this is /app/dist (baked in by the web-builder
    # stage). In a local dev checkout with `npm run build` it also gets
    # picked up; if neither produced a dist the mount is skipped and
    # vite dev (on the same :42069) serves the UI itself.
    spa_dist = _find_spa_dist() if include_spa else None
    if spa_dist is not None:
        # StaticFiles(html=True) only serves ``index.html`` when a .html
        # is requested and missing — it does NOT fallback arbitrary
        # client-side routes (e.g. ``/dashboard``, ``/chat/123``) to
        # the SPA shell. Anything ``<a href="/internal-link">`` does a
        # full-page nav that lands here and would 404 without an
        # explicit catch-all. Wire one up: anything GET that did not
        # match an API router and is not a real file in ``spa_dist``
        # is served the SPA shell.
        app.mount(
            "/",
            _SpaFallback(directory=str(spa_dist), html=True),
            name="spa",
        )
        logger.info("SPA mounted", extra={"path": str(spa_dist)})
    else:
        logger.info(
            "SPA dist not found; serving API only "
            "(run `npm run build` in app/ or use vite dev to serve the UI)"
        )

    return app


def create_runtime_app(*, bus: "Bus", workers: "WorkerRegistry") -> FastAPI:
    """Factory for the internal API served by every MAGI runtime.

    Both arguments are explicit: this module never reaches back into
    ``magi.startup`` to construct them — the composition root injects
    the already-built ``RuntimeContext`` here as its constituent parts.
    """
    return create_app(
        bus=bus,
        workers=workers,
        include_spa=False,
        include_control_routes=False,
    )


def create_control_app(*, context: ControlContext) -> FastAPI:
    """Factory for the singleton browser-facing service; it has no local MAGI state."""
    return create_app(
        bus=context.bus, include_spa=True, include_control_routes=True, include_private_routes=False
    )
