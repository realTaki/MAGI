"""MAGI unified startup package (refactored).

All startup-related code lives here. Provisioning and running are separate:
``magi init`` / ``magi node create`` write durable state, while node and WebUI
commands only open already-provisioned state.

- ``HOST_WORKSPACE_DIR``   — root of operator persistent data
- ``MAGI_NAME``            — display name (default ``eva-000``)
- ``MAGIS_NAME``           — local MAGIS storage name (default ``genesis``)
- ``MAGIS_DATABASE_URL``   — optional explicit MAGIS DSN for provisioning
- ``MAGI_ID``              — stored identity; runtime reads it from RuntimeSpec

Workspace = ``<HOST_WORKSPACE_DIR>/MAGI_Citizens/<MAGI_NAME>``.
The path is *derived*, never passed in.

Sub-modules:

- :mod:`startup.config`    — :class:`StartupConfig` + :class:`StartupContext` + parsing
- :mod:`startup.paths`     — host / workspace / DB path helpers
- :mod:`startup.provision` — explicit Genesis and node provisioning
- :mod:`startup.runtime`   — Runtime composition + serve
- :mod:`startup.local`     — local process management + OS detection
- :mod:`startup.webui`     — singleton WebUI lifecycle
- :mod:`startup.kubernetes` — K8s resource creation + orchestrator service
- :mod:`startup.cli`       — :command:`magi init|node|webui`
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    "config",
    "paths",
    "provision",
    "runtime",
    "local",
    "webui",
    "kubernetes",
    "cli",
]
