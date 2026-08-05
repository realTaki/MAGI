
"""SQLAlchemy declarative base for the MAGI ``db`` package.

Single ``Base.metadata`` is shared by every table module under
:mod:`magi.db.models_*` and the session-package tables at
:mod:`magi.bus.models.local.session`. Alembic uses this metadata for
autogeneration, while committed revisions own the actual schema changes.
Adding a model therefore also requires a reviewed Alembic revision.

Also exposes :func:`utcnow_naive` — the canonical
"replacement for ``datetime.utcnow()``" used by every
ORM ``default=`` and ``onupdate=`` in the project.
Lives here (rather than in
:mod:`magi.bus.protocols.session` where its sibling
``utcnow_iso`` lives) so the ORM model files can import
it without triggering ``magi.agent.memory.__init__`` —
which in turn imports the contact tools module, which
imports from ``magi.db``, which is mid-load. A
top-level db-package import keeps that loop closed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utcnow_naive() -> datetime:
    """Return the current UTC time as a **naive** datetime.

    Used by every ORM ``default=`` / ``onupdate=`` that
    stamps a row's ``created_at`` / ``updated_at``. The
    DB columns are typed ``DateTime`` (no tz) — switching
    them to ``DateTime(timezone=True)`` is a future
    Alembic migration task (the schema column type, the store-level ISO
    serialisation, and the cross-module ordering all move together).

    Until then this helper is the canonical "what
    replaces ``datetime.utcnow()``" answer: it returns the
    same naive UTC instant (DB column shape unchanged,
    on-disk bytes identical) but does so via
    ``datetime.now(timezone.utc)`` to silence Python
    3.12+'s ``datetime.utcnow()`` deprecation warning.

    Companion to :func:`magi.bus.protocols.session.utcnow_iso`,
    which renders the same moment as an ISO string for
    the session-package tables (which use ``String(32)``
    columns rather than ``DateTime``). Two helpers,
    one canonical UTC, two storage shapes — both are
    intentionally naive-UTC until a dedicated timezone-column revision.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """The single declarative base for every MAGI ORM table."""
