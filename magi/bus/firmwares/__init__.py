"""bus.firmwares — concrete Job Boards and Books.

Importing this package registers every firmware ORM table on
``Base.metadata``. :mod:`magi.bus.bases.db.schema` and the Alembic
environment rely on that side-effect before they walk the registry.

Subpackages
===========

- :mod:`.jobs`  — Job Boards (``publish → claim → submit_result``)
- :mod:`.books` — Books (local SQLite, MAGIS-shared, file-backed)
"""

from magi.bus.firmwares import jobs as jobs
from magi.bus.firmwares.books import local as local
from magi.bus.firmwares.books import magis as magis

__all__ = [
    "jobs",
    "local",
    "magis",
]
