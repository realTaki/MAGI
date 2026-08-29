"""MAGI — Modular Agentic Group Intelligence.

A group-intelligence system built from autonomous, independently-deployable
agents — every one of them is a **MAGI**. Two **archetypes** ship today:

- **ADAM** — *Autonomous Dispatch Agent Manager*, the **manager-archetype**
  MAGI. Runs the FastAPI control plane + Web frontend for the operator;
  owns workspace state; can dispatch / create / recall worker MAGIs.
- **EVA** — *Extended Virtual Agent*, the **worker-archetype** MAGI.
  One per assigned User; default channel Telegram; pulls workspace-wide
  data from its ADAM and caches locally.

Worker MAGIs do not call each other by default; coordination goes through
the manager MAGI. The runtime is the same; the archetype is configured
per-process via the ``magis`` table — ADAM = Genesis MAGI creator,
EVA = assigned to a parent MAGI. New archetypes can be added without
forking the runtime.

Naming: ``operator`` (lowercase) is the user role running ADAM's web UI —
it replaces the older "admin / HR / IT" framing. The people EVAs serve
are **assigned Users** (replaces the older "contact" framing so the
system can serve both B2B and B2C). There is intentionally **no CLI** —
all operator work goes through ADAM's web UI.
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
    import sys as _sys

    import pysqlite3 as _pysqlite3  # type: ignore[import-not-found]

    _sys.modules["sqlite3"] = _pysqlite3
    _sys.modules["sqlite3.dbapi2"] = _pysqlite3.dbapi2  # SQLAlchemy sometimes probes this
except ImportError:
    pass

__version__ = "0.1.0"
