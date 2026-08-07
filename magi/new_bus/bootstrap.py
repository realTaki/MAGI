"""Composition-root bootstrap for new_bus.

Provides :func:`bootstrap_new_bus` — a pure function that wires local
SQLite and MAGIS database access into a single :class:`NewBus` facade.
All paths are passed explicitly; no environment variable reads, no
auto-discovery.  The composition root (:mod:`magi.startup.runtime`)
calls this after resolving identity and database paths, then passes
the resulting ``NewBus`` to workers via constructor injection.

All Job/Book imports are **lazy** (inside ``_bootstrap_with_dirs``) so
that merely importing this module does not register ORM tables.  This
avoids ``extend_existing`` conflicts with the old bus at import time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from magi.new_bus.db.engine import EngineFactory, build_local_factory, build_magis_factory


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
        job = bus.tool_job_board.claim(worker_id="w1")
        magic_book = bus.magic_book.get(magic_id=1)  # None when MAGIS absent

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

    tasks_book: object  # TaskBook
    task_presets_book: object  # TaskPresetBook
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
    provider_config_job_board: object  # providerConfigJobBoard

    # -- local: contacts_book & memory_book (Notify boards) ----------------------------

    save_contact_notify_board: object  # contactNotifyBoard
    save_memory_notify_board: object  # rememberNotifyBoard

    # -- local: misc (Books) -------------------------------------------------

    token_usage_book: object  # TokenUsageBook
    action_items_book: object  # ActionItemBook
    hook_signoffs_book: object  # HookSignoffBook

    # -- internal factories (advanced / test use) ---------------------------
    # Positioned *before* defaulted fields so dataclass __init__ ordering
    # is satisfied (required fields must precede optional ones).

    _local_factory: EngineFactory = field(repr=False)
    _magis_factory: EngineFactory | None = field(repr=False, default=None)

    # -- magis_book: society tree (all Optional — None when MAGIS DB absent) ------

    magic_book: object | None = None  # MagicBook | None
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
) -> NewBus:
    """Explicitly bootstrap a ``NewBus`` with resolved paths.

    Called by the composition root (e.g. :mod:`magi.startup.runtime`)
    after identity + database paths have been resolved.  Does NOT
    read environment variables or call auto-discovery — all paths
    are passed explicitly (plan §10).

    Returns a ready-to-use ``NewBus``.  The caller is responsible for
    passing it to workers via constructor injection.
    """
    return _bootstrap_with_dirs(state_dir=state_dir, magis_url=magis_url)


def _bootstrap_with_dirs(
    *,
    state_dir: str,
    magis_url: str | None = None,
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
        TaskPresetBook,
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
        MagicBook,
        MagisAdminBook,
        MagisBook,
        MagisMembershipBook,
        MagisRoleBook,
        PortAllocationBook,
        WorkspaceArchiveBook,
    )
    from magi.new_bus.guild import (
        callLLMJobBoard,
        chatJobBoard,
        contactNotifyBoard,
        controlJobBoard,
        deliveryJobBoard,
        providerConfigJobBoard,
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
    memory_book = MemoryBook(local_factory)
    contacts_book = ContactBook(local_factory)
    contact_notes_book = ContactNoteBook(local_factory)
    settings_book = SettingBook(local_factory)
    tasks_book = TaskBook(local_factory)
    task_presets_book = TaskPresetBook(local_factory)
    task_runs_book = TaskRunBook(local_factory)
    tool_definitions_book = ToolDefinitionBook(local_factory)
    tool_catalog_book = ToolCatalogStateBook(local_factory)
    mcp_servers_book = McpServerBook(local_factory)
    token_usage_book = TokenUsageBook(local_factory)
    action_items_book = ActionItemBook(local_factory)
    hook_signoffs_book = HookSignoffBook(local_factory)

    # ---- local job boards ---------------------------------------------------
    agent_job_board = runAgentJobBoard(local_factory)
    tool_job_board = runToolJobBoard(local_factory)
    llm_job_board = callLLMJobBoard(local_factory)
    delivery_job_board = deliveryJobBoard(local_factory)
    a2a_job_board = sendA2AJobBoard(local_factory)
    chat_job_board = chatJobBoard(local_factory)
    control_job_board = controlJobBoard(local_factory)
    provider_config_job_board = providerConfigJobBoard(local_factory)

    # ---- local notify boards ------------------------------------------------
    set_config_notify_board = setConfigNotifyBoard(local_factory)
    set_setting_notify_board = setSettingNotifyBoard(local_factory)
    schedule_task_notify_board = scheduleTaskNotifyBoard(local_factory)
    save_contact_notify_board = contactNotifyBoard(local_factory)
    save_memory_notify_board = rememberNotifyBoard(local_factory)

    # ---- magis_book books -------------------------------------------------------
    if magis_factory is not None:
        magic_book = MagicBook(magis_factory)
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
        magic_book = None
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
        task_presets_book=task_presets_book,
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
        provider_config_job_board=provider_config_job_board,
        save_contact_notify_board=save_contact_notify_board,
        save_memory_notify_board=save_memory_notify_board,
        token_usage_book=token_usage_book,
        action_items_book=action_items_book,
        hook_signoffs_book=hook_signoffs_book,
        magic_book=magic_book,
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
