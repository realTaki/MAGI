"""Composition root for the one local MAGI Webapp service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api import router as api_router
from asp.app import create_operator
from localdb import LocalDatabase, default_database_path

ROOT = Path(__file__).resolve().parent
UI_DIST = ROOT / "ui" / "dist"


class WebService:
    """Own every resource exposed by Webapp's single FastAPI process."""

    def __init__(
        self,
        *,
        ui_dist: Path = UI_DIST,
        database_path: Path | None = None,
        asp_seed: dict[str, str] | None = None,
    ) -> None:
        self.ui_dist = ui_dist
        self.database = LocalDatabase(database_path or default_database_path())
        self.asp = create_operator(asp_seed or {})
        self.app = FastAPI(title="MAGI Webapp", version="0.1.0", lifespan=self._lifespan)
        self._install_routes()

    def _install_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> JSONResponse:
            return JSONResponse({"status": "ok"})

        self.app.include_router(api_router)
        self.app.include_router(self.asp.router)
        self.app.mount("/", StaticFiles(directory=self.ui_dist, html=True, check_dir=False), name="ui")

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        self.database.open()
        app.state.service = self
        app.state.localdb = self.database
        app.state.asp = self.asp
        try:
            yield
        finally:
            await self.asp.close()
            self.database.close()
