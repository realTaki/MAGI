"""MAGI unified startup package (refactored).

Per MAGI_UNIFIED_STARTUP_REFACTOR_PLAN_V2, all startup-related code lives
here. There are exactly four runtime inputs:

- ``HOST_WORKSPACE_DIR``   — root of operator persistent data
- ``MAGI_NAME``            — display name (default ``eva-000``)
- ``MAGIS_DATABASE_URL``   — MAGIS DSN (omit ⇒ bootstrap first MAGIS)
- ``MAGI_ID``              — MAGIS identity when joining an existing MAGIS

Workspace = ``<HOST_WORKSPACE_DIR>/MAGI_Citizens/<MAGI_NAME>``.
The path is *derived*, never passed in.

Sub-modules:

- :mod:`magi.startup.config`    — :class:`StartupConfig` + :class:`StartupContext` + parsing
- :mod:`magi.startup.paths`     — host / workspace / DB path helpers
- :mod:`magi.startup.bootstrap` — first/existing-MAGI bootstrap + control secret
- :mod:`magi.startup.runtime`   — Runtime composition + serve
- :mod:`magi.startup.local`     — local process management + OS detection
- :mod:`magi.startup.webui`     — singleton WebUI lifecycle
- :mod:`magi.startup.kubernetes` — K8s resource creation + orchestrator service
- :mod:`magi.startup.cli`       — :command:`magi run|create|start|stop|status|...`
"""

from __future__ import annotations

__all__ = [
    "config",
    "paths",
    "bootstrap",
    "runtime",
    "local",
    "webui",
    "kubernetes",
    "cli",
]