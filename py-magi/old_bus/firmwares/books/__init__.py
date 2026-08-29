"""bus.firmwares.books — concrete Books.

Each Book wraps one (or a small group of) ORM tables, or a file shelf.
Books are CRUD primitives: they do single-table operations. Cross-table
orchestration is the caller's responsibility (typically by chaining
writes inside one ``factory.session()`` block).

The Book/Record bases live in :mod:`bus.bases`, not here.

Subpackages
===========

- :mod:`.local` — Books for the local SQLite runtime database
  (conversation, contact, memory, task, tool, mcp, action_item,
  token_usage, setting, hook_signoff)
- :mod:`.magis` — Books for the shared MAGIS SQLite or PostgreSQL database
  (magis, membership, runtime, control)
- :mod:`.file`  — file-backed ``PromptBook`` and ``SkillsBook``
"""
