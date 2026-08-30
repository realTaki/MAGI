"""Run the single local MAGI Webapp FastAPI service."""

from __future__ import annotations

import os

import uvicorn

from .app import create_app


def main() -> int:
    host = os.environ.get("MAGI_WEBAPP_HOST", "127.0.0.1")
    port = int(os.environ.get("MAGI_WEBAPP_PORT", "42069"))
    uvicorn.run(create_app(), host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

