"""bus.bases — BUS primitives: job/book bases and the database layer.

This package owns the reusable contracts and storage machinery. Concrete
Job Boards and Books live in :mod:`magi.bus.firmwares` and depend on
these types; they do not live here.

Public surface:

- :class:`BaseJob` / :class:`BaseJobResult` / :class:`BaseJobRowMixin` /
  :class:`BaseJobBoard` / :class:`JobStatus`
- :class:`BaseBook` / :class:`BaseRecord` / :class:`BaseRecordMixin`
- :class:`BaseFileBook`
- :class:`StreamHub`

The database layer (:mod:`magi.bus.bases.db`) is the integration
surface only — ``Base``, engine factories, and ``FileShelf``. It does
not define tables, columns, or Alembic revisions; those live in
:mod:`magi.bus.firmwares`. Domain code is forbidden from importing
this subpackage.
"""

from magi.old_bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin
from magi.old_bus.bases.file_book import BaseFileBook
from magi.old_bus.bases.job import (
    BaseJob,
    BaseJobBoard,
    BaseJobResult,
    BaseJobRowMixin,
    JobStatus,
)
from magi.old_bus.bases.stream import StreamHub

__all__ = [
    "BaseBook",
    "BaseFileBook",
    "BaseJob",
    "BaseJobBoard",
    "BaseJobResult",
    "BaseJobRowMixin",
    "BaseRecord",
    "BaseRecordMixin",
    "JobStatus",
    "StreamHub",
]
