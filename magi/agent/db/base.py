"""SQLAlchemy declarative base for the MAGI ``db`` package.

Single ``Base.metadata`` is shared by every table module
under :mod:`magi.agent.db.models_*` and the session-package
tables at :mod:`magi.agent.memory.session.tables`. ``init_orm``
walks ``Base.metadata`` once at boot to ``create_all``,
so any new table just needs to import ``Base`` and
define a subclass — the rest is automatic.

Also exposes :func:`utcnow_naive` — the canonical
"replacement for ``datetime.utcnow()``" used by every
ORM ``default=`` and ``onupdate=`` in the project.
Lives here (rather than in
:mod:`magi.agent.memory.session.ids` where its sibling
``utcnow_iso`` lives) so the ORM model files can import
it without triggering ``magi.agent.memory.__init__`` —
which in turn imports the contact tools module, which
imports from ``magi.agent.db``, which is mid-load. A
top-level db-package import keeps that loop closed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


def utcnow_naive() -> datetime:
    """Return the current UTC time as a **naive** datetime.

    Used by every ORM ``default=`` / ``onupdate=`` that
    stamps a row's ``created_at`` / ``updated_at``. The
    DB columns are typed ``DateTime`` (no tz) — switching
    them to ``DateTime(timezone=True)`` is a future
    Alembic-migration task (the schema column type, the
    store-level ISO serialisation, the cross-module
    ordering all move together); see the project's
    "Alembic baseline → Next" roadmap entry.

    Until then this helper is the canonical "what
    replaces ``datetime.utcnow()``" answer: it returns the
    same naive UTC instant (DB column shape unchanged,
    on-disk bytes identical) but does so via
    ``datetime.now(timezone.utc)`` to silence Python
    3.12+'s ``datetime.utcnow()`` deprecation warning.

    Companion to :func:`magi.agent.memory.session.ids.utcnow_iso`,
    which renders the same moment as an ISO string for
    the session-package tables (which use ``String(32)``
    columns rather than ``DateTime``). Two helpers,
    one canonical UTC, two storage shapes — both are
    intentionally naive-UTC to keep SQLite column values
    stable across the eventual Alembic migration.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """The single declarative base for every MAGI ORM table."""
