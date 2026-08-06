"""Repository pattern — internal data-access layer for the BUS.

Each domain has a dedicated Repository class under
``db/repositories/local/`` (for the local SQLite runtime DB) or
``db/repositories/magis/`` (for the MAGIS PostgreSQL DB).  Repositories
own ORM rows and raw SQL; the only legitimate consumers are the
``magi/bus/jobs/services/`` facades, which translate Repository returns
into public DTOs.

External code (``magi.agent``, ``magi.tools``, ``magi.channels``,
…) must never import from this package — the AST import-boundary
test (``tests/architecture/test_import_boundaries.py``) enforces that.

Inventory
=========

Local SQLite (state_dir-anchored):

- ``local.session``      — ChatSession / ChatMessage CRUD, FTS search
- ``local.contact``      — Contact / ContactNote CRUD, search, bindings
- ``local.memory``       — MemoryEntry CRUD
- ``local.task``         — Task / TaskRun / TaskPreset CRUD + seed_presets
- ``local.tool``         — ToolCatalogState / ToolDefinitionRecord CRUD
- ``local.auth``         — contact-role queries
- ``local.action_item``  — ActionItem CRUD
- ``local.mcp``          — McpServer CRUD
- ``local.token_usage``  — TokenUsage CRUD
- ``local.connector``    — connector config CRUD
- ``local.hook``         — hook signoffs (legacy)

MAGIS PostgreSQL:

- ``magis.magis``        — MAGIS / MAGISRole / MAGISMembership CRUD
- ``magis.magic``        — MAGIC / EvaRuntime CRUD
- ``magis.auth``         — AuthCredential CRUD
- ``magis.control``      — ControlRuntimeState / port / secret repository
  (formerly ``magi.bus.db.control.repository``)

.. note::
   Only ``magis.control`` is populated in this refactor's first
   cut.  The remaining Repository classes will be filled in by a
   follow-up PR — see ``docs/bus-refactor-plan.md`` §Phase 5.
"""
