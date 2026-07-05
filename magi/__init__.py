"""MAGI — Modular Agentic Governed Intelligence.

Localized enterprise agent system where each employee gets a dedicated
Telegram-bound **EVE**. The package is split into three submodules:

- ``magi.adam``  — control plane (FastAPI app + Web frontend for HR / admin).
  Owns employee / eve / skill / knowledge / audit state, dispatch / recall
  orchestration, and the live control console.
- ``magi.eve``   — execution plane (TG bot, dynamic context, skill runner,
  proactive engine). One process / container per employee.
- ``magi.shared``— RPC contracts, event schemas, shared types reused by
  both sides.

Naming: MAGI is the system. Adam (control) and EVE (execution) are the two
node types. ``admin`` (lowercase) is the user role (HR / IT) using Adam's
web frontend. There is intentionally **no CLI** — all operator work goes
through Adam's web UI. EVE instances **do not** talk to each other; all
coordination is Adam ↔ EVE.
"""

# sqlite3 driver swap (D.18 — FTS5 / trigram).
# Run BEFORE any submodule imports sqlite3. ``local_db.py`` and
# ``settings.py`` both do ``import sqlite3`` at module top; if
# pysqlite3 is registered later they'd get the stdlib build while
# SQLAlchemy gets the bundled one — two different libs writing the
# same WAL DB is the kind of bug that surfaces three weeks in.
#
# pysqlite3-binary wheels guarantee FTS5 + trigram + JSON1 +
# RTREE; they do NOT ship ICU. If you need ICU segmentation you
# must build pysqlite3 from source against libicu and re-deploy.
#
# Best-effort: the shim silently no-ops when pysqlite3 is not
# installed (the stdlib sqlite3 in CPython 3.12+ has FTS5
# compiled in, so the chat-search FTS path still works).
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-not-found]
    import sys as _sys

    _sys.modules["sqlite3"] = _pysqlite3
    _sys.modules["sqlite3.dbapi2"] = _pysqlite3.dbapi2  # SQLAlchemy sometimes probes this
except ImportError:
    pass

__version__ = "0.1.0"