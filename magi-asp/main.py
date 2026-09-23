"""ASP server: composition root and process entry point.

``python main.py`` (what the desktop app runs) and the ``magi-asp`` console
script both land here. Routes live in :mod:`server`, the sqlite file in
:mod:`db`.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from db import LocalDatabase, default_database_path
from server.app import create_operator
from server.operator import load_or_create_operator
from server.spawn import MagiSpawner, default_spawner


def _intranet_base_url() -> str:
    """Origin MAGI processes use to reach this ASP.

    magi-asp is intranet by default (loopback). MAGI_ASP_PUBLIC_URL is only
    an override when the operator has already placed ASP on another bind.
    There is no public-network approval wizard on the spawn/invite path.
    """
    host = os.environ.get("MAGI_ASP_HOST", "127.0.0.1")
    port = os.environ.get("MAGI_ASP_PORT", "42069")
    return os.environ.get("MAGI_ASP_PUBLIC_URL") or f"http://{host}:{port}"


class AspServer:
    """Own the ASP routes and the ASP sqlite file."""

    def __init__(
        self,
        *,
        database_path: Path | None = None,
        asp_seed: dict[str, str] | None = None,
        magi_spawner: MagiSpawner | None = None,
        asp_base: str | None = None,
    ) -> None:
        self.database = LocalDatabase(database_path or default_database_path())
        self.operator_handle = "user"
        self.operator_token = ""
        self.spawner = magi_spawner if magi_spawner is not None else default_spawner()
        self.asp = create_operator(
            asp_seed or {},
            spawner=self.spawner,
            base_url=asp_base or _intranet_base_url(),
        )
        self.app = FastAPI(title="MAGI ASP", version="0.1.0", lifespan=self._lifespan)
        self.app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?|null",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._install_routes()

    def _install_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> JSONResponse:
            return JSONResponse({"status": "ok"})

        @self.app.get("/operator")
        async def operator() -> JSONResponse:
            return JSONResponse(
                {"handle": self.operator_handle, "token": self.operator_token}
            )

        self.app.include_router(self.asp.router)

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        self.database.open()
        handle, token = load_or_create_operator(self.database)
        self.operator_handle = handle
        self.operator_token = token
        self.asp.store.register_agent(handle, token)
        app.state.service = self
        app.state.localdb = self.database
        app.state.asp = self.asp
        try:
            yield
        finally:
            await self.asp.close()
            self.database.close()


def create_app(
    *,
    database_path: Path | None = None,
    asp_seed: dict[str, str] | None = None,
    magi_spawner=None,
    asp_base: str | None = None,
) -> FastAPI:
    """Create the ASP server application."""
    return AspServer(
        database_path=database_path,
        asp_seed=asp_seed,
        magi_spawner=magi_spawner,
        asp_base=asp_base,
    ).app


def main() -> int:
    # Intranet by default: loopback only. MAGI spawned here joins invites
    # from this ASP without a public-network approval or config wizard.
    host = os.environ.get("MAGI_ASP_HOST", "127.0.0.1")
    port = int(os.environ.get("MAGI_ASP_PORT", "42069"))
    uvicorn.run(create_app(), host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
