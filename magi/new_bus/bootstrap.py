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

import os
from dataclasses import dataclass, field
from typing import Optional

from magi.new_bus.db.engine import EngineFactory, build_local_factory, build_magis_factory


@dataclass(frozen=True, slots=True)
class NewBus:
    """Public, domain-partitioned BUS facade.

    Holds both local SQLite and MAGIS database access internally.
    Constructed by :func:`bootstrap_new_bus` in the composition root;
    workers receive a ready-to-use ``NewBus`` via constructor injection.

    Usage::

        bus = bootstrap_new_bus(state_dir="...", magis_url="...")
        job = bus.tool_jobs.claim(worker_id="w1")
        magic = bus.magic.get(magic_id=1)  # None when MAGIS absent

    When MAGIS is not configured, all magis-related fields are
    ``None``.
    """

    # -- local: sessions ----------------------------------------------------

    sessions: object  # SessionBook
    messages: object  # MessageBook

    # -- local: memory & contacts -------------------------------------------

    memory: object  # MemoryBook
    contacts: object  # ContactBook
    contact_notes: object  # ContactNoteBook

    # -- local: settings ----------------------------------------------------

    settings: object  # SettingBook
    set_config: object  # setConfigNotifyBoard
    set_setting: object  # setSettingNotifyBoard

    # -- local: tasks -------------------------------------------------------

    tasks: object  # TaskBook
    task_presets: object  # TaskPresetBook
    task_runs: object  # TaskRunBook
    schedule_task: object  # scheduleTaskNotifyBoard

    # -- local: tools & MCP -------------------------------------------------

    tool_definitions: object  # ToolDefinitionBook
    tool_catalog: object  # ToolCatalogStateBook
    mcp_servers: object  # McpServerBook
    tool_jobs: object  # runToolJobBoard

    # -- local: agent -------------------------------------------------------

    agent_runs: object  # runAgentJobBoard
    chat: object  # chatJobBoard

    # -- local: LLM ---------------------------------------------------------

    llm_jobs: object  # callLLMJobBoard

    # -- local: delivery & A2A -----------------------------------------------

    delivery: object  # deliveryJobBoard
    a2a: object  # sendA2AJobBoard

    # -- local: control signals ---------------------------------------------

    control: object  # controlJobBoard

    # -- local: contacts & memory (write path, fire-and-forget) --------------

    save_contact: object  # contactNotifyBoard
    save_memory: object  # rememberNotifyBoard

    # -- local: misc ---------------------------------------------------------

    token_usage: object  # TokenUsageBook
    action_items: object  # ActionItemBook
    hook_signoffs: object  # HookSignoffBook

    # -- internal factories (advanced / test use) ---------------------------
    # Positioned *before* defaulted fields so dataclass __init__ ordering
    # is satisfied (required fields must precede optional ones).

    _local_factory: EngineFactory = field(repr=False)
    _magis_factory: EngineFactory | None = field(repr=False, default=None)

    # -- magis: society tree (all Optional — None when MAGIS DB absent) ------

    magic: object | None = None  # MagicBook | None
    magis: object | None = None  # MagisBook | None
    magis_admins: object | None = None  # MagisAdminBook | None
    memberships: object | None = None  # MagisMembershipBook | None
    roles: object | None = None  # MagisRoleBook | None

    # -- magis: runtimes ----------------------------------------------------

    eva_runtimes: object | None = None  # EvaRuntimeBook | None
    control_runtimes: object | None = None  # ControlRuntimeBook | None
    control_secrets: object | None = None  # ControlSecretBook | None
    port_allocations: object | None = None  # PortAllocationBook | None
    workspace_archives: object | None = None  # WorkspaceArchiveBook | None

    # -- magis: auth --------------------------------------------------------

    auth_credentials: object | None = None  # AuthCredentialBook | None


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

    url = magis_url or os.environ.get("MAGIS_DATABASE_URL")
    magis_factory = build_magis_factory(url) if url else None

    # ---- local books -------------------------------------------------------
    sessions = SessionBook(local_factory)
    messages = MessageBook(local_factory)
    memory = MemoryBook(local_factory)
    contacts = ContactBook(local_factory)
    contact_notes = ContactNoteBook(local_factory)
    settings = SettingBook(local_factory)
    tasks = TaskBook(local_factory)
    task_presets = TaskPresetBook(local_factory)
    task_runs = TaskRunBook(local_factory)
    tool_definitions = ToolDefinitionBook(local_factory)
    tool_catalog = ToolCatalogStateBook(local_factory)
    mcp_servers = McpServerBook(local_factory)
    token_usage = TokenUsageBook(local_factory)
    action_items = ActionItemBook(local_factory)
    hook_signoffs = HookSignoffBook(local_factory)

    # ---- local jobs --------------------------------------------------------
    agent_runs = runAgentJobBoard(local_factory)
    tool_jobs = runToolJobBoard(local_factory)
    llm_jobs = callLLMJobBoard(local_factory)
    delivery = deliveryJobBoard(local_factory)
    a2a = sendA2AJobBoard(local_factory)
    chat = chatJobBoard(local_factory)
    control = controlJobBoard(local_factory)
    set_config = setConfigNotifyBoard(local_factory)
    set_setting = setSettingNotifyBoard(local_factory)
    schedule_task = scheduleTaskNotifyBoard(local_factory)
    save_contact = contactNotifyBoard(local_factory)
    save_memory = rememberNotifyBoard(local_factory)

    # ---- magis books -------------------------------------------------------
    if magis_factory is not None:
        magic = MagicBook(magis_factory)
        magis = MagisBook(magis_factory)
        magis_admins = MagisAdminBook(magis_factory)
        memberships = MagisMembershipBook(magis_factory)
        roles = MagisRoleBook(magis_factory)
        eva_runtimes = EvaRuntimeBook(magis_factory)
        control_runtimes = ControlRuntimeBook(magis_factory)
        control_secrets = ControlSecretBook(magis_factory)
        port_allocations = PortAllocationBook(magis_factory)
        workspace_archives = WorkspaceArchiveBook(magis_factory)
        auth_credentials = AuthCredentialBook(magis_factory)
    else:
        magic = None
        magis = None
        magis_admins = None
        memberships = None
        roles = None
        eva_runtimes = None
        control_runtimes = None
        control_secrets = None
        port_allocations = None
        workspace_archives = None
        auth_credentials = None

    # ---- assemble ----------------------------------------------------------
    return NewBus(
        sessions=sessions,
        messages=messages,
        memory=memory,
        contacts=contacts,
        contact_notes=contact_notes,
        settings=settings,
        set_config=set_config,
        set_setting=set_setting,
        tasks=tasks,
        task_presets=task_presets,
        task_runs=task_runs,
        schedule_task=schedule_task,
        tool_definitions=tool_definitions,
        tool_catalog=tool_catalog,
        mcp_servers=mcp_servers,
        tool_jobs=tool_jobs,
        agent_runs=agent_runs,
        chat=chat,
        llm_jobs=llm_jobs,
        delivery=delivery,
        a2a=a2a,
        control=control,
        save_contact=save_contact,
        save_memory=save_memory,
        token_usage=token_usage,
        action_items=action_items,
        hook_signoffs=hook_signoffs,
        magic=magic,
        magis=magis,
        magis_admins=magis_admins,
        memberships=memberships,
        roles=roles,
        eva_runtimes=eva_runtimes,
        control_runtimes=control_runtimes,
        control_secrets=control_secrets,
        port_allocations=port_allocations,
        workspace_archives=workspace_archives,
        auth_credentials=auth_credentials,
        _local_factory=local_factory,
        _magis_factory=magis_factory,
    )
