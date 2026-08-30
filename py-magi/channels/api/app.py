"""The small public HTTP surface of one MAGI runtime.

This package is deliberately not an operator WebUI or a MAGIS control plane.
It exposes only the transactions a local MAGI App needs against one selected
runtime: liveness, creating a conversation, and publishing a chat turn.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from channels.api import chat, health
from channels.api.errors import install_error_handler
from startup import __version__

if TYPE_CHECKING:
    from bus import Bus


def create_runtime_app(*, bus: Bus) -> FastAPI:
    """Build the public API of exactly one running MAGI.

    The runtime's worker lifecycle remains outside HTTP. The App calls this
    service only when it explicitly creates a remote conversation or sends a
    turn; all App configuration and cached conversation metadata stay local.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield

    app = FastAPI(
        title="MAGI Runtime",
        version=__version__,
        summary="One MAGI's conversation API.",
        lifespan=lifespan,
    )
    app.state.bus = bus
    install_error_handler(app)
    app.include_router(health.router)
    app.include_router(chat.router, prefix="/api")
    return app
