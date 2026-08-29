"""bus.firmwares — concrete Job Boards, Books, and their schema.

Importing this package registers every firmware ORM table on
``Base.metadata``. :mod:`magi.bus.firmwares.schema` and the Alembic
environment rely on that side-effect before they walk the registry.

This package owns every business table and column definition.
:mod:`magi.bus.bases.db` only supplies the engine / ORM integration.

Subpackages
===========

- :mod:`.jobs`    — Job Boards (``publish → claim → submit_result``)
- :mod:`.books`   — Books (local SQLite, MAGIS-shared, file-backed)
- :mod:`.alembic` — revisioned DDL for those tables
"""

from magi.old_bus.firmwares import jobs as jobs
from magi.old_bus.firmwares.books import local as local
from magi.old_bus.firmwares.books import magis as magis

__all__ = [
    "jobs",
    "local",
    "magis",
]
