"""SQLAlchemy declarative base for the MAGI ``db`` package.

Single ``Base.metadata`` is shared by every table module
under :mod:`magi.agent.db.models_*` and the session-package
tables at :mod:`magi.agent.session.tables`. ``init_orm``
walks ``Base.metadata`` once at boot to ``create_all``,
so any new table just needs to import ``Base`` and
define a subclass — the rest is automatic.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """The single declarative base for every MAGI ORM table."""
