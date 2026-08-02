"""Lock the SQLite PRAGMA configuration required by design §15.

Each connection must set ``WAL``, ``busy_timeout=5000``,
``foreign_keys=ON``, and ``synchronous=NORMAL``. SQLite resets
``synchronous`` whenever ``journal_mode`` changes, so the order
on the connection listener matters.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def test_local_db_sets_synchronous_normal(tmp_path: Path) -> None:
    """The legacy raw-SQLite bootstrap sets synchronous=NORMAL."""
    from magi.db.local_db import init_sqlite

    init_sqlite(tmp_path)

    raw = sqlite3.connect(str(tmp_path / "magi.db"))
    try:
        # PRAGMA synchronous returns the *current* effective value as
        # an integer: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA. NORMAL is 1.
        assert raw.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert raw.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        raw.close()


def test_engine_sets_synchronous_normal_on_new_connection(tmp_path: Path) -> None:
    """The SQLAlchemy engine's per-connection listener sets NORMAL."""
    from magi.db import init_orm, open_session

    init_orm(str(tmp_path), seed_root=False)

    with open_session() as session:
        # Each open_session() opens a fresh pooled connection,
        # which the listener mutates with PRAGMAs.
        sync = session.execute(
            __import__("sqlalchemy").text("PRAGMA synchronous")
        ).scalar()
        journal = session.execute(
            __import__("sqlalchemy").text("PRAGMA journal_mode")
        ).scalar()
        busy = session.execute(
            __import__("sqlalchemy").text("PRAGMA busy_timeout")
        ).scalar()
        fk = session.execute(
            __import__("sqlalchemy").text("PRAGMA foreign_keys")
        ).scalar()
    assert sync == 1, f"synchronous should be NORMAL (1), got {sync}"
    assert str(journal).lower() == "wal"
    assert busy == 5000
    assert int(fk) == 1