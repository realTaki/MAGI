"""Composition-root bootstrap for new_bus.

Provides :func:`bootstrap_new_bus` — a pure function that wires local
SQLite, MAGIS database, and file-storage access into a single
:class:`NewBus` facade.  All paths are passed explicitly; no
environment variable reads, no auto-discovery.  The composition root
(:mod:`magi.startup.runtime`) calls this after resolving identity and
database paths, then passes the resulting ``NewBus`` to workers via
constructor injection.

All Job/Book imports are **lazy** (inside ``_bootstrap_with_dirs``) so
that merely importing this module does not register ORM tables.  This
avoids ``extend_existing`` conflicts with the old bus at import time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from magi.new_bus.db.engine import EngineFactory, build_local_factory, build_magis_factory

logger = logging.getLogger("magi.new_bus.bootstrap")


@dataclass(frozen=True, slots=True)
class NewBus:
    """Public, domain-partitioned BUS facade.

    Holds both local SQLite and MAGIS database access internally.
    Constructed by :func:`bootstrap_new_bus` in the composition root;
    workers receive a ready-to-use ``NewBus`` via constructor injection.

    Naming conventions
    ------------------
    - ``*_job_board``   — full round-trip (publish → claim → submit_result)
    - ``*_notify_board`` — fire-and-forget (publish only, no result tracking)
    - plain nouns       — Book (CRUD without another worker involved)

    Usage::

        bus = bootstrap_new_bus(state_dir="...", magis_url="...")
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

    # -- local: settings_book (Book + Notify boards) ------------------------------

    settings_book: object  # SettingBook
    set_config_notify_board: object  # setConfigNotifyBoard
    set_setting_notify_board: object  # setSettingNotifyBoard

    # -- local: tasks_book (Books + Notify board) ---------------------------------
    #
    # ``tasks_book`` owns BOTH user-created tasks
    # (``Task.source == SOURCE_USER``) and preset templates
    # (``Task.source == SOURCE_PROACTIVE``); the old separate
    # ``task_presets_book`` field has been folded into this
    # single Book (parallel to the ``action_items`` refactor).

    tasks_book: object  # TaskBook
    task_runs_book: object  # TaskRunBook
    schedule_task_notify_board: object  # scheduleTaskNotifyBoard

    # -- local: tools & MCP (Books + Job board) ------------------------------

    tool_definitions_book: object  # ToolDefinitionBook
    tool_catalog_book: object  # ToolCatalogStateBook
    mcp_servers_book: object  # McpServerBook
    tool_job_board: object  # runToolJobBoard

    # -- local: agent (Job boards) -------------------------------------------

    agent_job_board: object  # runAgentJobBoard
    chat_job_board: object  # chatJobBoard

    # -- local: LLM (Job board) ----------------------------------------------

    llm_job_board: object  # callLLMJobBoard

    # -- local: delivery & A2A (Job boards) ----------------------------------

    delivery_job_board: object  # deliveryJobBoard
    a2a_job_board: object  # sendA2AJobBoard

    # -- local: control (Job boards) -----------------------------------------

    control_job_board: object  # controlJobBoard
    change_provider_config_job_board: object  # changeProviderConfigJobBoard

    # -- local: contacts_book & memory_book (Notify boards) ----------------------------

    save_contact_notify_board: object  # contactNotifyBoard
    save_memory_notify_board: object  # rememberNotifyBoard

    # -- local: streaming ---------------------------------------------------

    stream_hub: object  # StreamHub

    # -- local: misc (Books) -------------------------------------------------

    token_usage_book: object  # TokenUsageBook
    action_items_book: object  # ActionItemBook
    hook_signoffs_book: object  # HookSignoffBook

    # -- local: prompts (File-backed Book) ----------------------------------

    prompt_book: object  # PromptBook

    # -- internal factories (advanced / test use) ---------------------------
    # Positioned *before* defaulted fields so dataclass __init__ ordering
    # is satisfied (required fields must precede optional ones).

    _local_factory: EngineFactory = field(repr=False)
    _magis_factory: EngineFactory | None = field(repr=False, default=None)

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


def bootstrap_new_bus(
    *,
    state_dir: str,
    magis_url: str | None = None,
    prompts_dir: str | None = None,
) -> NewBus:
    """Explicitly bootstrap a ``NewBus`` with resolved paths.

    Called by the composition root (e.g. :mod:`magi.startup.runtime`)
    after identity + database paths have been resolved.  Does NOT
    read environment variables or call auto-discovery — all paths
    are passed explicitly (plan §10).

    If *prompts_dir* is ``None``, the bundled ``magi/prompts/``
    directory is auto-detected from the package location.

    Returns a ready-to-use ``NewBus``.  The caller is responsible for
    passing it to workers via constructor injection.
    """
    return _bootstrap_with_dirs(
        state_dir=state_dir, magis_url=magis_url, prompts_dir=prompts_dir,
    )


def _bootstrap_with_dirs(
    *,
    state_dir: str,
    magis_url: str | None = None,
    prompts_dir: str | None = None,
) -> NewBus:
    """Wire the BUS with explicit paths (for tests).

    All Job/Book imports are lazy (inside this function) to avoid
    registering ORM tables at module-import time, which would conflict
    with the old bus's table definitions via ``extend_existing``.
    """
    # ---- lazy imports (avoid eager ORM table registration) ----------------
    from magi.new_bus.library.local import (
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
    from magi.new_bus.library.magis import (
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
    from magi.new_bus.guild import (
        callLLMJobBoard,
        changeProviderConfigJobBoard,
        chatJobBoard,
        contactNotifyBoard,
        controlJobBoard,
        deliveryJobBoard,
        rememberNotifyBoard,
        runAgentJobBoard,
        runToolJobBoard,
        scheduleTaskNotifyBoard,
        sendA2AJobBoard,
        setConfigNotifyBoard,
        setSettingNotifyBoard,
    )

    # ---- wire factories ----------------------------------------------------
    local_factory = build_local_factory(state_dir)

    # Pure pass-through: caller is the composition root and owns path
    # resolution.  No env reads — ``magis_url=None`` simply means
    # "no MAGIS database configured" (test / single-MAGIS scenarios).
    magis_factory = build_magis_factory(magis_url) if magis_url else None

    # ---- local books -------------------------------------------------------
    sessions_book = SessionBook(local_factory)
    messages_book = MessageBook(local_factory)
    # Idempotent: creates every new_bus ORM table on the local SQLite
    # file (``chat_sessions`` / ``chat_messages`` / etc.). In a
    # mixed-bus deployment the old bus's alembic migration 0001 has
    # already created these tables, so this is a no-op; in a
    # new_bus-only bootstrap (or a fresh test state dir) it lays
    # them down so the FTS triggers below have something to attach
    # to.
    local_factory.create_all()
    # Install the FTS5 virtual table + sync triggers (idempotent —
    # every statement uses ``IF NOT EXISTS``). The old bus's alembic
    # migration 0001 also creates these; whichever path ran first
    # wins and the other is a no-op. Calling here means tests that
    # build a fresh SQLite file via ``EngineFactory.create_all`` get
    # a working search index too.
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
        from magi.new_bus.db.file import FileStore
        from magi.new_bus.library.file.promptBook import PromptBook

        prompt_store = FileStore(_prompts_dir)
        prompt_book = PromptBook(prompt_store)

        # Seed SOUL.md into the workspace if missing.  The convention is
        # ``state_dir = <workspace>/memories``, so workspace is one level up.
        _ensure_workspace_soul(Path(state_dir).parent, _prompts_dir)
    else:
        prompt_book = None

    # ---- stream hub (in-process pipe registry) ------------------------------
    from magi.new_bus.stream import StreamHub

    stream_hub = StreamHub()

    # ---- local job boards ---------------------------------------------------
    agent_job_board = runAgentJobBoard(local_factory)
    tool_job_board = runToolJobBoard(local_factory)
    llm_job_board = callLLMJobBoard(local_factory)
    delivery_job_board = deliveryJobBoard(local_factory)
    a2a_job_board = sendA2AJobBoard(local_factory)
    chat_job_board = chatJobBoard(local_factory)
    control_job_board = controlJobBoard(local_factory)
    change_provider_config_job_board = changeProviderConfigJobBoard(
        local_factory, settings_book=settings_book
    )

    # ---- local notify boards ------------------------------------------------
    set_config_notify_board = setConfigNotifyBoard(local_factory)
    set_setting_notify_board = setSettingNotifyBoard(local_factory)
    schedule_task_notify_board = scheduleTaskNotifyBoard(local_factory)
    save_contact_notify_board = contactNotifyBoard(local_factory)
    save_memory_notify_board = rememberNotifyBoard(local_factory)

    # ---- magis_book books -------------------------------------------------------
    if magis_factory is not None:
        magis_book = MagisBook(magis_factory)
        magis_admins_book = MagisAdminBook(magis_factory)
        memberships_book = MagisMembershipBook(magis_factory)
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
    return NewBus(
        sessions_book=sessions_book,
        messages_book=messages_book,
        memory_book=memory_book,
        contacts_book=contacts_book,
        contact_notes_book=contact_notes_book,
        settings_book=settings_book,
        set_config_notify_board=set_config_notify_board,
        set_setting_notify_board=set_setting_notify_board,
        tasks_book=tasks_book,
        task_runs_book=task_runs_book,
        schedule_task_notify_board=schedule_task_notify_board,
        tool_definitions_book=tool_definitions_book,
        tool_catalog_book=tool_catalog_book,
        mcp_servers_book=mcp_servers_book,
        tool_job_board=tool_job_board,
        agent_job_board=agent_job_board,
        chat_job_board=chat_job_board,
        llm_job_board=llm_job_board,
        delivery_job_board=delivery_job_board,
        a2a_job_board=a2a_job_board,
        control_job_board=control_job_board,
        change_provider_config_job_board=change_provider_config_job_board,
        save_contact_notify_board=save_contact_notify_board,
        save_memory_notify_board=save_memory_notify_board,
        token_usage_book=token_usage_book,
        action_items_book=action_items_book,
        hook_signoffs_book=hook_signoffs_book,
        prompt_book=prompt_book,
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

    # Last resort: try relative to *this* file (``new_bus/bootstrap.py``).
    # ``magi/new_bus/bootstrap.py`` → ``magi/`` → ``magi/prompts/``
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
    now it's part of the new_bus bootstrap so file seeding stays
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
