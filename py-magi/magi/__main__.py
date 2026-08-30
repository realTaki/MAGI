"""Run the MAGI FastAPI service."""

from __future__ import annotations

import os

import uvicorn

from magi.service import Magi


def main() -> int:
    service = Magi(workspace=os.environ.get("MAGI_WORKSPACE", "workspace"))
    uvicorn.run(
        service.app,
        host=os.environ.get("MAGI_HOST", "127.0.0.1"),
        port=int(os.environ.get("MAGI_PORT", "42070")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
