"""ASP server process entry point."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from service import AspServer


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
