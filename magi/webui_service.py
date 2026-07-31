"""Standalone, stateless unified WebUI service entry point."""

from __future__ import annotations

import logging
import os

import uvicorn

from magi.constants import DEFAULT_LOG_LEVEL, WEBUI_HOST, WEBUI_PORT


def run() -> None:
    logging.basicConfig(
        level=DEFAULT_LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from magi.agent.db.magis import init_magis_public_db

    # Seeding is idempotent. This service owns initial control-plane startup;
    # keeping it here also works when it starts before the first runtime.
    init_magis_public_db(seed_root=True)
    port = int(os.environ.get("MAGI_PORT", str(WEBUI_PORT)))
    reload = os.environ.get("MAGI_RELOAD", "0") == "1"
    uvicorn.run(
        "magi.channels.webui.app:create_control_app",
        factory=True,
        host=WEBUI_HOST,
        port=port,
        log_level=DEFAULT_LOG_LEVEL,
        reload=reload,
        reload_dirs=["/app/magi"] if reload else None,
    )
