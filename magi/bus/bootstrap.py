"""Composition-root bootstrap for bus.

Provides :func:`bootstrap_bus` — a pure function that wires local
SQLite, MAGIS database, and file-storage access into a single
:class:`Bus` facade.  All paths are passed explicitly; no
environment variable reads, no auto-discovery.  The composition root
(:mod:`magi.startup.runtime`) calls this after resolving identity and
database paths, then passes the resulting ``Bus`` to workers via
constructor injection.

No process-level singleton — every component receives its ``Bus``
explicitly via constructor injection.

All Job/Book imports are **lazy** (inside ``_bootstrap_with_dirs``) so
that merely importing this module does not register ORM tables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from magi.bus.db.engine import EngineFactory, build_local_factory, build_magis_factory

logger = logging.getLogger("magi.bus.bootstrap")


@dataclass(frozen=True, slots=True)
class Bus:
    """Public, domain-partitioned bus facade.

    Holds both local SQLite and MAGIS database access internally.
    Constructed by :func:`bootstrap_bus` in the composition root;
    workers receive a ready-to-use ``Bus`` via constructor injection.

    Naming conventions
    ------------------
    - ``*_job_board``   — full round-trip (publish → claim → submit_result)
    - plain nouns       — Book (CRUD without another worker involved)

    Usage::

        bus = bootstrap_bus(state_dir="...", magis_url="...")
        job = bus.tool_job_board.claim()
        adam = bus.memberships_book.get(magic_id=1)  # ADAM = membership id=1

    When MAGIS is not configured, all magis_book-related fields are
    ``None``.
    """

    # -- local: sessions_book (Books) ---------------------------------------------

    sessions_book: object  # SessionBook
    messages_book: object  # MessageBook

    # -- local: memory_book & contacts_book (Books) ------------------------------------

    memory_book: object  # MemoryBook
    contacts_book: object  # ContactBook
    contact_notes_book: object  # ContactNoteBook

    # -- local: settings_book (Book) -----------------------------------------

    settings_book: object  # SettingBook

    # -- local: tasks_book (Books) --------------------------------------------
    #
    # ``tasks_book`` owns BOTH user-created tasks
    # (``Task.source == SOURCE_USER``) and preset templates
    # (``Task.source == SOURCE_PROACTIVE``); the old separate
    # ``task_presets_book`` field has been folded into this
    # single Book (parallel to the ``action_items`` refactor).

    tasks_book: object  # TaskBook
    task_runs_book: object  # TaskRunBook

    # -- local: tools & MCP (Books + Job board) ------------------------------

    tool_definitions_book: object  # ToolDefinitionBook
    tool_catalog_book: object  # ToolCatalogStateBook
    mcp_servers_book: object  # McpServerBook
    mcp_server_changed_job_board: object  # mcpServerChangedJobBoard
    tool_job_board: object  # runToolJobBoard

    # -- local: agent (Job board) ---------------------------------------------

    agent_job_board: object  # chatJobBoard

    # -- local: LLM (Job board) ----------------------------------------------

    llm_job_board: object  # callLLMJobBoard

    # -- local: delivery & A2A (Job boards) ----------------------------------

    delivery_job_board: object  # deliveryJobBoard
    a2a_job_board: object  # sendA2AJobBoard

    # -- local: provider config (Job board) ----------------------------------

    change_provider_config_job_board: object  # changeProviderConfigJobBoard

    # -- local: streaming ---------------------------------------------------

    stream_hub: object  # StreamHub

    # -- local: proactive (Job board) ---------------------------------------

    seed_preset_tasks_job_board: object  # seedPresetTasksJobBoard

    # -- local: task trigger (Job board) -----------------------------------

    run_task_job_board: object  # runTaskJobBoard

    # -- local: misc (Books) -------------------------------------------------

    token_usage_book: object  # TokenUsageBook
    action_items_book: object  # ActionItemBook
    hook_signoffs_book: object  # HookSignoffBook

    # -- internal factories (advanced / test use) ---------------------------
    # Positioned *before* defaulted fields so dataclass __init__ ordering
    # is satisfied (required fields must precede optional ones).

    _local_factory: EngineFactory = field(repr=False)
    _magis_factory: EngineFactory | None = field(repr=False, default=None)

    # -- local: prompts (File-backed Book) ----------------------------------

    prompt_book: object | None = None  # PromptBook | None

    # -- local: skills (File-backed Book; two roots: bundle + operator) ----

    skills_book: object | None = None  # SkillsBook | None

    # -- magis_book: society tree (all Optional — None when MAGIS DB absent) ------

    magis_book: object | None = None  # MagisBook | None
    magis_admins_book: object | None = None  # MagisAdminBook | None
    memberships_book: object | None = None  # MagisMembershipBook | None
    roles_book: object | None = None  # MagisRoleBook | None

    # -- magis_book: runtimes (Books) --------------------------------------------

    eva_runtimes_book: object | None = None  # EvaRuntimeBook | None
    control_runtimes_book: object | None = None  # ControlRuntimeBook | None
    control_secrets_book: object | None = None  # ControlSecretBook | None
    port_allocations_book: object | None = None  # PortAllocationBook | None
    workspace_archives_book: object | None = None  # WorkspaceArchiveBook | None

    # -- magis_book: auth (Book) --------------------------------------------------

    auth_credentials_book: object | None = None  # AuthCredentialBook | None


# ---------------------------------------------------------------------------
# public bootstrap entry point
# ---------------------------------------------------------------------------


def bootstrap_bus(
    *,
    state_dir: str,
    magis_url: str | None = None,
    prompts_dir: str | None = None,
) -> Bus:
    """Explicitly bootstrap a ``Bus`` with resolved paths.

    Called by the composition root (e.g. :mod:`magi.startup.runtime`)
    after identity + database paths have been resolved.  Does NOT
    read environment variables or call auto-discovery — all paths
    are passed explicitly.

    If *prompts_dir* is ``None``, the bundled ``magi/prompts/``
    directory is auto-detected from the package location.

    Returns a ready-to-use ``Bus``.  The caller is responsible for
    passing it to workers via constructor injection.  There is no
    process-level singleton — every component receives its ``Bus``
    explicitly.
    """
    return _bootstrap_with_dirs(
        state_dir=state_dir, magis_url=magis_url, prompts_dir=prompts_dir,
    )


def _bootstrap_with_dirs(
    *,
    state_dir: str,
    magis_url: str | None = None,
    prompts_dir: str | None = None,
) -> Bus:
    """Wire the bus with explicit paths (for tests).

    All Job/Book imports are lazy (inside this function) to avoid
    registering ORM tables at module-import time.
    """
    # ---- lazy imports (avoid eager ORM table registration) ----------------
    from magi.bus.library.local import (
        ActionItemBook,
        ContactBook,
        ContactNoteBook,
        HookSignoffBook,
        McpServerBook,
        MemoryBook,
        MessageBook,
        SessionBook,
        SettingBook,
        TaskBook,
        TaskRunBook,
        TokenUsageBook,
        ToolCatalogStateBook,
        ToolDefinitionBook,
    )
    from magi.bus.library.magis import (
        AuthCredentialBook,
        ControlRuntimeBook,
        ControlSecretBook,
        EvaRuntimeBook,
        MagisAdminBook,
        MagisBook,
        MagisMembershipBook,
        MagisRoleBook,
        PortAllocationBook,
        WorkspaceArchiveBook,
    )
    from magi.bus.guild import (
        callLLMJobBoard,
        changeProviderConfigJobBoard,
        deliveryJobBoard,
        mcpServerChangedJobBoard,
        chatJobBoard,
        runTaskJobBoard,
        runToolJobBoard,
        seedPresetTasksJobBoard,
        sendA2AJobBoard,
    )
    from magi.bus.db.file import FileShelf
    from magi.bus.library.file.promptBook import PromptBook
    from magi.bus.library.file.skillsBook import build_default_skills_book

    # ---- wire factories ----------------------------------------------------
    local_factory = build_local_factory(state_dir)

    # Pure pass-through: caller is the composition root and owns path
    # resolution.  No env reads — ``magis_url=None`` simply means
    # "no MAGIS database configured" (test / single-MAGIS scenarios).
    magis_factory = build_magis_factory(magis_url) if magis_url else None
    if magis_factory is not None:
        # MAGIS Books own their schema as well as local Books.  A Bus-only
        # runtime must not depend on another bootstrap to create it.
        # The Book modules were imported above, so all MAGIS ORM tables are
        # registered before this idempotent create-all call.
        magis_factory.create_all()

    # ---- local books -------------------------------------------------------
    sessions_book = SessionBook(local_factory)
    messages_book = MessageBook(local_factory)
    # Idempotent: creates every bus ORM table on the local SQLite
    # file (``chat_sessions`` / ``chat_messages`` / etc.).
    # In a fresh deployment this lays down the tables so the
    # FTS triggers below have something to attach to; on an
    # existing database ``create_all`` is a no-op.
    local_factory.create_all()
    # Install the FTS5 virtual table + sync triggers (idempotent —
    # every statement uses ``IF NOT EXISTS``). Calling here means
    # tests that build a fresh SQLite file via
    # ``EngineFactory.create_all`` get a working search index too.
    messages_book.ensure_fts()
    memory_book = MemoryBook(local_factory)
    contacts_book = ContactBook(local_factory)
    contact_notes_book = ContactNoteBook(local_factory)
    settings_book = SettingBook(local_factory)
    tasks_book = TaskBook(local_factory)
    task_runs_book = TaskRunBook(local_factory)
    tool_definitions_book = ToolDefinitionBook(local_factory)
    tool_catalog_book = ToolCatalogStateBook(local_factory)
    mcp_servers_book = McpServerBook(local_factory)
    token_usage_book = TokenUsageBook(local_factory)
    action_items_book = ActionItemBook(local_factory)
    hook_signoffs_book = HookSignoffBook(local_factory)

    # ---- prompt book (file-backed, not ORM) --------------------------------
    _prompts_dir = _resolve_prompts_dir(prompts_dir)
    if _prompts_dir is not None:
        prompt_shelf = FileShelf(_prompts_dir)
        prompt_book = PromptBook(prompt_shelf)

        # Seed SOUL.md into the workspace if missing.  The convention is
        # ``state_dir = <workspace>/memories``, so workspace is one level up.
        _ensure_workspace_soul(Path(state_dir).parent, _prompts_dir)
    else:
        prompt_book = None

    # ---- skills book (file-backed, two roots: bundle + operator) ---------
    # Convention: workspace is ``state_dir.parent`` (``<workspace>/memories``
    # is the state dir). Operator skills live at ``<workspace>/skills/``;
    # the bundle ships inside the ``magi`` package at ``<magi>/skills/``.
    # ``ensure_workspace`` (run by the composition root) creates the
    # operator ``skills/`` subdir before we get here, so we pass
    # ``create_root=False`` (set inside ``build_default_skills_book``) to
    # avoid a duplicate ``mkdir``.
    workspace_dir = Path(state_dir).parent
    skills_book = build_default_skills_book(workspace_dir)

    # ---- stream hub (in-process pipe registry) ------------------------------
    from magi.bus.stream import StreamHub

    stream_hub = StreamHub()

    # ---- local job boards ---------------------------------------------------
    agent_job_board = chatJobBoard(local_factory)
    tool_job_board = runToolJobBoard(local_factory)
    llm_job_board = callLLMJobBoard(local_factory)
    delivery_job_board = deliveryJobBoard(local_factory)
    a2a_job_board = sendA2AJobBoard(local_factory)
    change_provider_config_job_board = changeProviderConfigJobBoard(
        local_factory, settings_book=settings_book
    )
    mcp_server_changed_job_board = mcpServerChangedJobBoard(local_factory)
    seed_preset_tasks_job_board = seedPresetTasksJobBoard(local_factory)
    run_task_job_board = runTaskJobBoard(local_factory)

    # ---- magis_book books -------------------------------------------------------
    if magis_factory is not None:
        magis_book = MagisBook(magis_factory)
        magis_admins_book = MagisAdminBook(magis_factory)
        # ``MagisMembershipBook.instruction_context`` reads the per-MAGI
        # personal instruction from the local SettingBook (agent-worker-
        # bus.md §6). Inject it so the Book owns the join, not the
        # caller.
        memberships_book = MagisMembershipBook(
            magis_factory, settings_book=settings_book,
        )
        roles_book = MagisRoleBook(magis_factory)
        eva_runtimes_book = EvaRuntimeBook(magis_factory)
        control_runtimes_book = ControlRuntimeBook(magis_factory)
        control_secrets_book = ControlSecretBook(magis_factory)
        port_allocations_book = PortAllocationBook(magis_factory)
        workspace_archives_book = WorkspaceArchiveBook(magis_factory)
        auth_credentials_book = AuthCredentialBook(magis_factory)
    else:
        magis_book = None
        magis_admins_book = None
        memberships_book = None
        roles_book = None
        eva_runtimes_book = None
        control_runtimes_book = None
        control_secrets_book = None
        port_allocations_book = None
        workspace_archives_book = None
        auth_credentials_book = None

    # ---- assemble ----------------------------------------------------------
    return Bus(
        sessions_book=sessions_book,
        messages_book=messages_book,
        memory_book=memory_book,
        contacts_book=contacts_book,
        contact_notes_book=contact_notes_book,
        settings_book=settings_book,
        tasks_book=tasks_book,
        task_runs_book=task_runs_book,
        tool_definitions_book=tool_definitions_book,
        tool_catalog_book=tool_catalog_book,
        mcp_servers_book=mcp_servers_book,
        mcp_server_changed_job_board=mcp_server_changed_job_board,
        tool_job_board=tool_job_board,
        agent_job_board=agent_job_board,
        llm_job_board=llm_job_board,
        delivery_job_board=delivery_job_board,
        a2a_job_board=a2a_job_board,
        change_provider_config_job_board=change_provider_config_job_board,
        seed_preset_tasks_job_board=seed_preset_tasks_job_board,
        run_task_job_board=run_task_job_board,
        token_usage_book=token_usage_book,
        action_items_book=action_items_book,
        hook_signoffs_book=hook_signoffs_book,
        prompt_book=prompt_book,
        skills_book=skills_book,
        stream_hub=stream_hub,
        magis_book=magis_book,
        magis_admins_book=magis_admins_book,
        memberships_book=memberships_book,
        roles_book=roles_book,
        eva_runtimes_book=eva_runtimes_book,
        control_runtimes_book=control_runtimes_book,
        control_secrets_book=control_secrets_book,
        port_allocations_book=port_allocations_book,
        workspace_archives_book=workspace_archives_book,
        auth_credentials_book=auth_credentials_book,
        _local_factory=local_factory,
        _magis_factory=magis_factory,
    )


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _resolve_prompts_dir(prompts_dir: str | None) -> Path | None:
    """Resolve the bundled prompts directory.

    If *prompts_dir* is explicitly provided, use it as-is.
    Otherwise auto-detect from the ``magi`` package location.
    Returns ``None`` if auto-detection fails (e.g. in a test
    environment where ``magi`` isn't a regular package).
    """
    if prompts_dir is not None:
        return Path(prompts_dir)

    # Auto-detect: ``magi/prompts/`` relative to the magi package root.
    try:
        import magi
        candidate = Path(magi.__file__).resolve().parent / "prompts"
        if candidate.is_dir():
            return candidate
    except Exception:
        pass

    # Last resort: try relative to *this* file (``bus/bootstrap.py``).
    # ``magi/bus/bootstrap.py`` → ``magi/`` → ``magi/prompts/``
    try:
        candidate = Path(__file__).resolve().parent.parent / "prompts"
        if candidate.is_dir():
            return candidate
    except Exception:
        pass

    logger.debug("Could not auto-detect prompts directory; prompt_book will be None")
    return None


def _ensure_workspace_soul(workspace_dir: Path, prompts_dir: Path) -> None:
    """Copy the bundled ``soul.md`` into *workspace_dir* if missing.

    Idempotent — if ``<workspace>/SOUL.md`` already exists, this is
    a no-op.  Previously this lived in :func:`magi.startup.paths.ensure_workspace`;
    now it's part of the bus bootstrap so file seeding stays
    alongside the bus composition that consumes those files.
    """
    soul = workspace_dir / "SOUL.md"
    if soul.exists():
        return

    bundled = prompts_dir / "soul.md"
    if bundled.is_file():
        try:
            workspace_dir.mkdir(parents=True, exist_ok=True)
            soul.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("SOUL.md seeded from %s", bundled)
        except OSError:
            logger.exception("Failed to seed SOUL.md from %s", bundled)
    else:
        logger.warning("Bundled soul.md missing at %s; SOUL.md not created", bundled)
