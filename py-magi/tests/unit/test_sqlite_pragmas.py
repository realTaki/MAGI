"""Bus SQLite engine settings."""

from __future__ import annotations

from sqlalchemy import text


def test_bus_engine_sets_required_sqlite_pragmas(tmp_path) -> None:
    from magi.old_bus.bases.db.engine import build_local_factory

    factory = build_local_factory(str(tmp_path))
    with factory.session() as session:
        sync = session.execute(text("PRAGMA synchronous")).scalar()
        journal = session.execute(text("PRAGMA journal_mode")).scalar()
        busy = session.execute(text("PRAGMA busy_timeout")).scalar()
        foreign_keys = session.execute(text("PRAGMA foreign_keys")).scalar()

    assert sync == 1
    assert str(journal).lower() == "wal"
    assert busy == 5000
    assert int(foreign_keys) == 1
