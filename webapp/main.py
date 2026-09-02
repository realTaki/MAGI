"""The one local MAGI Webapp FastAPI application and process entry point."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from service import UI_DIST, WebService

def create_app(
    *,
    ui_dist: Path = UI_DIST,
    database_path: Path | None = None,
    asp_seed: dict[str, str] | None = None,
) -> FastAPI:
    """Create the one local application served on localhost:42069."""
    return WebService(ui_dist=ui_dist, database_path=database_path, asp_seed=asp_seed).app


def main() -> int:
    host = os.environ.get("MAGI_WEBAPP_HOST", "127.0.0.1")
    port = int(os.environ.get("MAGI_WEBAPP_PORT", "42069"))
    uvicorn.run(create_app(), host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
