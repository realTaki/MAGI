"""Local-SQLite Repository classes — one per domain.

Each module in this package exposes a single Repository class that
encapsulates all SQLAlchemy access for its domain.  See
:mod:`magi.bus.db.repositories` for the full inventory and the
boundary-test rules that keep external modules from importing
these directly.
"""
