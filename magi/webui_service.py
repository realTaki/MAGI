"""Standalone unified WebUI service entry point.

It deliberately uses the same ``magi`` image as every MAGI runtime.  Its
private workspace is only for WebUI login/onboarding state; selected MAGI data
is reached through the authenticated Runtime API proxy.
"""

from __future__ import annotations

import logging
import os

import uvicorn

from magi.constants import DEFAULT_LOG_LEVEL, STATE_DIR, WEBUI_HOST, WEBUI_PORT


def run() -> None:
    logging.basicConfig(
        level=DEFAULT_LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from magi.agent.db import init_orm, init_sqlite
    from magi.agent.db.magis import init_magis_public_db
    from magi.agent.workspace import bootstrap_workspace, workspace_root

    init_sqlite(STATE_DIR)
    init_orm(STATE_DIR, seed_root=False)
    # Seeding is idempotent. This service owns initial control-plane startup;
    # keeping it here also works when it starts before the first runtime.
    init_magis_public_db(seed_root=True)
    bootstrap_workspace(workspace_root(STATE_DIR))
    port = int(os.environ.get("MAGI_PORT", str(WEBUI_PORT)))
    reload = os.environ.get("MAGI_RELOAD", "0") == "1"
    uvicorn.run(
        "magi.channels.webui.app:create_app",
        factory=True,
        host=WEBUI_HOST,
        port=port,
        log_level=DEFAULT_LOG_LEVEL,
        reload=reload,
        reload_dirs=["/app/magi"] if reload else None,
    )
