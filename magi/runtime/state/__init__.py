"""MAGI runtime state — SQLite (this package) + Postgres (C1+)."""

from magi.runtime.state.local_db import init_sqlite

__all__ = ["init_sqlite"]