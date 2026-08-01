"""Deprecated compatibility namespace for :mod:`magi.db`.

Database implementation is shared infrastructure and now lives outside the
agent runtime. New code must import ``magi.db`` directly.
"""

import importlib
import sys


_MODULES = ("base", "alembic_runner", "migrations")

def __getattr__(name: str):
    """Forward names added while :mod:`magi.db` is still initialising."""
    import magi.db as _db
    return getattr(_db, name)


for _name in _MODULES:
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(f"magi.db.{_name}")

from magi.db import *  # noqa: F403
