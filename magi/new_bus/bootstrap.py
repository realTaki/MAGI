"""Composition-root bootstrap for new_bus.

Provides the process-wide ``NewBus`` singleton that wires local SQLite
and MAGIS database access into a single facade.  Workers and business
modules call :func:`get_bus` — they never need to know database paths
or URLs.

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
    Workers and business modules access domain services directly
    without knowing which database backs them::

        from magi.new_bus import get_bus

        bus = get_bus()
        job = bus.tool_jobs.claim(worker_id="w1")
        magic = bus.magic.get(magic_id=1)

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
    set_config: object  # setConfigJob
    set_setting: object  # setSettingJob

    # -- local: tasks -------------------------------------------------------

    tasks: object  # TaskBook
    task_presets: object  # TaskPresetBook
    task_runs: object  # TaskRunBook
    schedule_task: object  # scheduleTaskJob

    # -- local: tools & MCP -------------------------------------------------

    tool_definitions: object  # ToolDefinitionBook
    tool_catalog: object  # ToolCatalogStateBook
    mcp_servers: object  # McpServerBook
    tool_jobs: object  # runToolJob

    # -- local: agent -------------------------------------------------------

    agent_runs: object  # runAgentJob
    chat: object  # chatJob

    # -- local: LLM ---------------------------------------------------------

    llm_jobs: object  # callLLMJob

    # -- local: delivery & A2A -----------------------------------------------

    delivery: object  # deliveryJob
    a2a: object  # sendA2AJob

    # -- local: control signals ---------------------------------------------

    control: object  # controlJob

    # -- local: contacts & memory (write path, fire-and-forget) --------------

    save_contact: object  # contactJob
    save_memory: object  # rememberJob

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
# singleton
# ---------------------------------------------------------------------------

_bus: NewBus | None = None
_injected_magis_url: str | None = None


def set_magis_url(url: str | None) -> None:
    """Inject a MAGIS database URL for the next :func:`get_bus` call.

    Call **before** the first :func:`get_bus`.  Used by tests and the
    CLI Profile to supply a per-MAGIS SQLite path instead of relying
    on the K8s ``MAGIS_DATABASE_URL`` environment variable.
    """
    global _injected_magis_url
    _injected_magis_url = url


def _discover_magis_url() -> str | None:
    """Discover the MAGIS database URL.

    1. Explicitly injected URL (tests / CLI Profile).
    2. ``MAGIS_DATABASE_URL`` env var (K8s Profile).
    """
    if _injected_magis_url is not None:
        return _injected_magis_url
    return os.environ.get("MAGIS_DATABASE_URL")


def get_bus() -> NewBus:
    """Return the process-wide ``NewBus`` singleton.

    Auto-discovers the local SQLite path (via
    :func:`magi.startup.paths.resolve_state_dir`) and the MAGIS database URL
    (via ``MAGIS_DATABASE_URL`` or :func:`set_magis_url`).

    This is the **only** entry point that modules outside
    ``magi.new_bus`` should use.  Workers receive a ready-to-use
    ``NewBus`` without ever knowing where the databases live.
    """
    global _bus
    if _bus is not None:
        return _bus
    _bus = _bootstrap()
    return _bus


def _bootstrap() -> NewBus:
    """Wire the full BUS with both databases.

    Internal — callers use :func:`get_bus`.  Tests that need a
    different state directory can call :func:`_bootstrap_with_dirs`.
    """
    # Plan §6 — startup.paths is the composition-root path resolver.
    from magi.startup.paths import resolve_state_dir as _state_dir
    state = str(_state_dir())

    return _bootstrap_with_dirs(state_dir=state)


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
        callLLMJob,
        chatJob,
        contactJob,
        controlJob,
        deliveryJob,
        rememberJob,
        runAgentJob,
        runToolJob,
        scheduleTaskJob,
        sendA2AJob,
        setConfigJob,
        setSettingJob,
    )

    # ---- wire factories ----------------------------------------------------
    local_factory = build_local_factory(state_dir)

    url = magis_url or _discover_magis_url()
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
    agent_runs = runAgentJob(local_factory)
    tool_jobs = runToolJob(local_factory)
    llm_jobs = callLLMJob(local_factory)
    delivery = deliveryJob(local_factory)
    a2a = sendA2AJob(local_factory)
    chat = chatJob(local_factory)
    control = controlJob(local_factory)
    set_config = setConfigJob(local_factory)
    set_setting = setSettingJob(local_factory)
    schedule_task = scheduleTaskJob(local_factory)
    save_contact = contactJob(local_factory)
    save_memory = rememberJob(local_factory)

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
