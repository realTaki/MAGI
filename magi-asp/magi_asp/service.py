"""Composition root for the ASP server process."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from magi_asp.asp.app import create_operator
from magi_asp.localdb import LocalDatabase, default_database_path


class AspServer:
    """Own the ASP routes and the ASP sqlite file."""

    def __init__(
        self,
        *,
        database_path: Path | None = None,
        asp_seed: dict[str, str] | None = None,
    ) -> None:
        self.database = LocalDatabase(database_path or default_database_path())
        self.asp = create_operator(asp_seed or {})
        self.app = FastAPI(title="MAGI ASP", version="0.1.0", lifespan=self._lifespan)
        self._install_routes()

    def _install_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> JSONResponse:
            return JSONResponse({"status": "ok"})

        self.app.include_router(self.asp.router)

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
