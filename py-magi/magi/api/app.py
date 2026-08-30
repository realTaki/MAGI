"""The small public HTTP surface of one MAGI runtime."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from magi import __version__
from magi.api import chat, health
from magi.api.errors import install_error_handler

if TYPE_CHECKING:
    from bus import Bus


def create_runtime_app(*, bus: Bus) -> FastAPI:
    """Build the public API of exactly one running MAGI."""

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
