"""bus.library — read-side data access layer (Books).

Each Book wraps one (or a small group of) ORM tables.  Books are
CRUD primitives: they do single-table operations.  Cross-table
orchestration is the caller's responsibility (typically by chaining
``book.add_in(session, ...)`` calls in one ``factory.session()`` block).

Subpackages
===========

- :mod:`.local`   — Books for the local SQLite runtime database
  (session, contact, memory, task, tool, mcp, action_item, token_usage,
  setting, hook_signoff)
- :mod:`.magis`   — Books for the shared MAGIS SQLite or PostgreSQL database
  (magis, membership, runtime, auth_credential, control)
"""

from magi.bus.library.base import BaseBook, BaseRecord, BaseRecordMixin, record

__all__ = ["BaseBook", "BaseRecord", "BaseRecordMixin"]
