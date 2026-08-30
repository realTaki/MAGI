"""The one local MAGI Webapp FastAPI application and process entry point."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from localdb import LocalDatabase, default_database_path

ROOT = Path(__file__).resolve().parent
UI_DIST = ROOT / "ui" / "dist"


def create_app(*, ui_dist: Path = UI_DIST, database_path: Path | None = None) -> FastAPI:
    """Create the one local application served on localhost:42069."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = LocalDatabase(database_path or default_database_path())
        database.open()
        app.state.localdb = database
        try:
            yield
        finally:
            database.close()

    app = FastAPI(title="MAGI Webapp", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # `api` and `asp` routers are registered before this SPA fallback.
    app.mount("/", StaticFiles(directory=ui_dist, html=True, check_dir=False), name="ui")
    return app


def main() -> int:
    host = os.environ.get("MAGI_WEBAPP_HOST", "127.0.0.1")
    port = int(os.environ.get("MAGI_WEBAPP_PORT", "42069"))
    uvicorn.run(create_app(), host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
