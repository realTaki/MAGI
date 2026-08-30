"""FastAPI composition root for the local MAGI Webapp."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
UI_DIST = ROOT / "ui" / "dist"


def create_app(*, ui_dist: Path = UI_DIST) -> FastAPI:
    """Create the one local application served on localhost:42069."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield

    app = FastAPI(title="MAGI Webapp", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # API and ASP routers are registered before this SPA fallback.
    app.mount("/", StaticFiles(directory=ui_dist, html=True, check_dir=False), name="ui")
    return app

