"""Composition-root opening for BUS.

Provides :func:`open_bus` — a pure function that opens a workspace's local
SQLite, MAGIS database, and file-storage access into a single
:class:`Bus` facade.  All paths are passed explicitly; no
environment variable reads, no auto-discovery.  The composition root
(:mod:`startup.runtime`) calls this after resolving identity and
database paths, then passes the resulting ``Bus`` to workers via
constructor injection.

No process-level singleton — every component receives its ``Bus``
explicitly via constructor injection.

All Job/Book imports are **lazy** (inside ``_open_with_dirs``) so
that merely importing this module does not register ORM tables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, overload

from old_bus.bases.db.engine import EngineFactory, build_local_factory, build_magis_factory

if TYPE_CHECKING:
    from old_bus.bases.stream import StreamHub
    from old_bus.firmwares.books.file.promptBook import PromptBook
    from old_bus.firmwares.books.file.skillsBook import SkillsBook
    from old_bus.firmwares.books.local.actionItemBook import ActionItemBook
    from old_bus.firmwares.books.local.contactBook import ContactBook, ContactNoteBook
    from old_bus.firmwares.books.local.conversationBook import (
        ConversationBook,
        MessageBook,
    )
    from old_bus.firmwares.books.local.hookSignoffBook import HookSignoffBook
    from old_bus.firmwares.books.local.mcpServerBook import McpServerBook
    from old_bus.firmwares.books.local.memoryBook import MemoryBook
    from old_bus.firmwares.books.local.settingBook import SettingBook
    from old_bus.firmwares.books.local.tasksBook import TaskBook, TaskRunBook
    from old_bus.firmwares.books.local.tokenUsageBook import TokenUsageBook
    from old_bus.firmwares.books.local.toolsBook import (
        ToolCatalogStateBook,
        ToolDefinitionBook,
    )
    from old_bus.firmwares.books.magis.controlSettingBook import ControlSettingBook
    from old_bus.firmwares.books.magis.magisBook import MagisAdminBook, MagisBook
    from old_bus.firmwares.books.magis.membershipBook import (
        MagisMembershipBook,
        MagisRoleBook,
    )
    from old_bus.firmwares.books.magis.runtimeBook import (
        ControlSecretBook,
        RuntimeBook,
    )
    from old_bus.firmwares.jobs.a2aJob import a2aNotifyBoard, a2aRequestJobBoard
    from old_bus.firmwares.jobs.callLLMJob import callLLMJobBoard
    from old_bus.firmwares.jobs.changeMCPServerJob import changeMCPServerJobBoard
    from old_bus.firmwares.jobs.changeProviderConfigJob import changeProviderConfigJobBoard
    from old_bus.firmwares.jobs.chatNotifyJob import chatNotifyBoard
    from old_bus.firmwares.jobs.deliveryNotifyJob import deliveryNotifyJobBoard
    from old_bus.firmwares.jobs.runTaskJob import runTaskJobBoard
    from old_bus.firmwares.jobs.runToolJob import runToolJobBoard
    from old_bus.firmwares.jobs.seedPresetTasksJob import seedPresetTaskJobBoard

logger = logging.getLogger("bus.bootstrap")


@dataclass(frozen=True, slots=True)
class Bus:
    """Public, domain-partitioned bus facade.

    Holds both local SQLite and MAGIS database access internally.
    Constructed by :func:`open_bus` in the composition root;
    workers receive a ready-to-use ``Bus`` via constructor injection.

    Naming conventions
    ------------------
    - ``*_job_board``   — full round-trip (publish → claim → submit_result)
    - plain nouns       — Book (CRUD without another worker involved)

    Usage::

        bus = open_bus(workspace_dir="...", magis_url="...")
        job = bus.tool_job_board.claim(worker_id="worker-1")
        adam = bus.memberships_book.get(1)  # ADAM = membership id=1

    When MAGIS is not configured, all magis_book-related fields are
    ``None``.
    """

    # -- local: conversations_book (Books) -----------------------------------------
    # conversations_book is an instance of ConversationBook.

    conversations_book: ConversationBook  # ConversationBook
    messages_book: MessageBook  # MessageBook

    # -- local: memory_book & contacts_book (Books) ------------------------------------

    memory_book: MemoryBook  # MemoryBook
    contacts_book: ContactBook  # ContactBook
    contact_notes_book: ContactNoteBook  # ContactNoteBook

    # -- local: settings_book (Book) -----------------------------------------

    settings_book: SettingBook  # SettingBook

    # -- local: tasks_book (Books) --------------------------------------------
    #
    # ``tasks_book`` owns BOTH user-created tasks
    # (``Task.source == TaskSource.USER``) and preset templates
    # (``Task.source == TaskSource.PROACTIVE``); the old separate
    # ``task_presets_book`` field has been folded into this
    # single Book (parallel to the ``action_items`` refactor).

    tasks_book: TaskBook  # TaskBook
    task_runs_book: TaskRunBook  # TaskRunBook

    # -- local: tools & MCP (Books + Job board) ------------------------------

    tool_definitions_book: ToolDefinitionBook  # ToolDefinitionBook
    tool_catalog_book: ToolCatalogStateBook  # ToolCatalogStateBook
    mcp_servers_book: McpServerBook  # McpServerBook
    change_mcp_server_job_board: changeMCPServerJobBoard  # changeMCPServerJobBoard
    tool_job_board: runToolJobBoard  # runToolJobBoard

    # -- local: agent (Job board) ---------------------------------------------

    agent_job_board: chatNotifyBoard  # chatNotifyBoard

    # -- local: LLM (Job board) ----------------------------------------------

    llm_job_board: callLLMJobBoard  # callLLMJobBoard

    # -- local: delivery (Job board) ------------------------------------------

    delivery_notify_job_board: deliveryNotifyJobBoard  # deliveryNotifyJobBoard

    # -- MAGIS shared: durable A2A boards -------------------------------------

    a2a_request_job_board: a2aRequestJobBoard | None
    a2a_notify_job_board: a2aNotifyBoard | None

    # -- local: provider config (Job board) ----------------------------------

    change_provider_config_job_board: changeProviderConfigJobBoard  # changeProviderConfigJobBoard

    # -- local: streaming ---------------------------------------------------

    stream_hub: StreamHub  # StreamHub

    # -- local: proactive (Job board) ---------------------------------------

    seed_preset_task_job_board: seedPresetTaskJobBoard  # seedPresetTaskJobBoard (one job per preset)

    # -- local: task trigger (Job board) -----------------------------------

    run_task_job_board: runTaskJobBoard  # runTaskJobBoard

    # -- local: misc (Books) -------------------------------------------------

    token_usage_book: TokenUsageBook  # TokenUsageBook
    action_items_book: ActionItemBook  # ActionItemBook
    hook_signoffs_book: HookSignoffBook  # HookSignoffBook

    # -- local: prompts (workspace-backed File Book) ------------------------

    prompt_book: PromptBook

    # -- internal factories (advanced / test use) ---------------------------
    # Positioned *before* defaulted fields so dataclass __init__ ordering
    # is satisfied (required fields must precede optional ones).

    _local_factory: EngineFactory = field(repr=False)
    _magis_factory: EngineFactory | None = field(repr=False, default=None)

    # -- local: skills (File-backed Book; two roots: bundle + operator) ----

    skills_book: SkillsBook | None = None  # SkillsBook | None

    # -- magis_book: society tree (all Optional — None when MAGIS DB absent) ------

    magis_book: MagisBook | None = None  # MagisBook | None
    magis_admins_book: MagisAdminBook | None = None  # MagisAdminBook | None
    memberships_book: MagisMembershipBook | None = None  # MagisMembershipBook | None
    roles_book: MagisRoleBook | None = None  # MagisRoleBook | None

    # -- magis_book: runtimes (Books) --------------------------------------------

    runtime_state_book: RuntimeBook | None = None  # RuntimeBook | None
    control_secrets_book: ControlSecretBook | None = None  # ControlSecretBook | None

    # -- magis_book: control-plane state (Book) -----------------------------------

    control_settings_book: ControlSettingBook | None = None  # MAGIS control-plane KV


@dataclass(frozen=True, slots=True)
class MagisBus:
    """Database-only facade returned by ``open_bus(magis_url=...)``.

    It has no node-private SQLite database,
    workspace, file shelves, workers, or stream hub.  It is the only facade
    the WebUI and startup control operations may use.
    """

    magis_book: MagisBook
    magis_admins_book: MagisAdminBook
    memberships_book: MagisMembershipBook
    roles_book: MagisRoleBook
    runtime_state_book: RuntimeBook
    control_secrets_book: ControlSecretBook
    control_settings_book: ControlSettingBook
    _magis_factory: EngineFactory = field(repr=False)


# ---------------------------------------------------------------------------
# public open entry point
# ---------------------------------------------------------------------------


@overload
def open_bus(
    *,
    workspace_dir: str,
    magis_url: str | None = None,
) -> Bus:
    ...


@overload
def open_bus(
    *,
    workspace_dir: None = None,
    magis_url: str,
) -> MagisBus:
    ...


def open_bus(
    *,
    workspace_dir: str | None = None,
    magis_url: str | None = None,
) -> Bus | MagisBus:
    """Open a provisioned ``Bus`` for one workspace.

    Called by the composition root (e.g. :mod:`startup.runtime`)
    after identity + database paths have been resolved.  Does NOT
    read environment variables or call auto-discovery.  When ``workspace_dir``
    is provided, the private SQLite store is always
    ``<workspace_dir>/memories/magi.db``.  Without ``workspace_dir``, it
    opens only the MAGIS control-plane facade and creates no local workspace.

    Before returning the facade, this function synchronises all existing BUS
    stores.  That happens before any Book or JobBoard is constructed, so a
    startup or code-reload cannot let another module query a stale table.
    Topology/workspace provisioning remains separate in
    :mod:`bus.provision`; this function does not create node identity or
    default application settings.

    Returns a ready-to-use ``Bus`` for a workspace or a MAGIS-only
    ``MagisBus``.  The caller is responsible for passing a workspace Bus to
    workers via constructor injection.  There is no process-level singleton.
    """
    if workspace_dir is None:
        if not magis_url:
            raise ValueError("MAGIS-only BUS requires a MAGIS database URL")
        return _build_magis_facade(magis_url)
    return _open_with_dirs(
        state_dir=str(Path(workspace_dir) / "memories"),
        magis_url=magis_url,
    )


def _build_magis_facade(magis_url: str) -> MagisBus:
    """Build the MAGIS-only view used by :func:`open_bus` without a workspace."""
    from old_bus.firmwares.books.magis import (
        ControlSecretBook,
        ControlSettingBook,
        MagisAdminBook,
        MagisBook,
        MagisMembershipBook,
        MagisRoleBook,
        RuntimeBook,
    )
    from old_bus.firmwares.schema import MAGIS_SCOPE, synchronise_schema

    factory = build_magis_factory(magis_url)
    synchronise_schema(factory, scope=MAGIS_SCOPE)
    return MagisBus(
        magis_book=MagisBook(factory),
        magis_admins_book=MagisAdminBook(factory),
        memberships_book=MagisMembershipBook(factory),
        roles_book=MagisRoleBook(factory),
        runtime_state_book=RuntimeBook(factory),
        control_secrets_book=ControlSecretBook(factory),
        control_settings_book=ControlSettingBook(factory),
        _magis_factory=factory,
    )


def _open_with_dirs(
    *,
    state_dir: str,
    magis_url: str | None = None,
    allow_unprovisioned: bool = False,
) -> Bus:
    """Wire the bus with explicit paths (for tests).

    All Job/Book imports are lazy (inside this function) to avoid
    registering ORM tables at module-import time.
    """
    # ---- lazy imports (avoid eager ORM table registration) ----------------
    from old_bus.bases.db.file import FileShelf
    from old_bus.firmwares.books.file.promptBook import PromptBook
    from old_bus.firmwares.books.file.skillsBook import build_default_skills_book
    from old_bus.firmwares.books.local import (
        ActionItemBook,
        ContactBook,
        ContactNoteBook,
        ConversationBook,
        HookSignoffBook,
        McpServerBook,
        MemoryBook,
        MessageBook,
        SettingBook,
        TaskBook,
        TaskRunBook,
        TokenUsageBook,
        ToolCatalogStateBook,
        ToolDefinitionBook,
    )
    from old_bus.firmwares.books.magis import (
        ControlSecretBook,
        ControlSettingBook,
        MagisAdminBook,
        MagisBook,
        MagisMembershipBook,
        MagisRoleBook,
        RuntimeBook,
    )
    from old_bus.firmwares.jobs import (
        a2aNotifyBoard,
        a2aRequestJobBoard,
        callLLMJobBoard,
        changeMCPServerJobBoard,
        changeProviderConfigJobBoard,
        chatNotifyBoard,
        deliveryNotifyJobBoard,
        runTaskJobBoard,
        runToolJobBoard,
        seedPresetTaskJobBoard,
    )

    # ---- wire factories ----------------------------------------------------
    state_path = Path(state_dir)
    if not allow_unprovisioned:
        database_path = state_path / "magi.db"
        if not database_path.is_file():
            from old_bus.provision import StorageNotProvisioned

            raise StorageNotProvisioned(
                f"node database is missing at {database_path}; run the explicit provisioning command"
            )
    local_factory = build_local_factory(state_dir)

    # Pure pass-through: caller is the composition root and owns path
    # resolution.  No env reads — ``magis_url=None`` simply means
    # "no MAGIS database configured" (test / single-MAGIS scenarios).
    magis_factory = build_magis_factory(magis_url) if magis_url else None
    # Schema synchronisation is the bootstrap barrier: do this before any
    # Book/JobBoard exists, and therefore before workers or HTTP handlers can
    # query the database. Every explicit Runtime restart passes this barrier
    # again.
    from old_bus.firmwares.schema import LOCAL_SCOPE, MAGIS_SCOPE, synchronise_schema

    if (
        magis_factory is not None
        and local_factory.url == magis_factory.url
    ):
        raise ValueError("MAGI-local and MAGIS stores must use distinct database URLs")
    synchronise_schema(local_factory, scope=LOCAL_SCOPE)
    if magis_factory is not None:
        synchronise_schema(magis_factory, scope=MAGIS_SCOPE)

    # ---- local books -------------------------------------------------------
    settings_book = SettingBook(local_factory)
    conversations_book = ConversationBook(local_factory, settings_book=settings_book)
    messages_book = MessageBook(local_factory, settings_book=settings_book)
    memory_book = MemoryBook(local_factory)
    contacts_book = ContactBook(local_factory)
    contact_notes_book = ContactNoteBook(local_factory)
    tasks_book = TaskBook(local_factory)
    task_runs_book = TaskRunBook(local_factory)
    tool_definitions_book = ToolDefinitionBook(local_factory)
    tool_catalog_book = ToolCatalogStateBook(local_factory)
    mcp_servers_book = McpServerBook(local_factory)
    token_usage_book = TokenUsageBook(local_factory)
    action_items_book = ActionItemBook(local_factory)
    hook_signoffs_book = HookSignoffBook(local_factory)

    # ---- prompt book (workspace-backed, not ORM) ---------------------------
    _workspace_dir = Path(state_dir).parent
    prompt_shelf = FileShelf(_workspace_dir / "prompts", create_root=False)
    prompt_book = PromptBook(prompt_shelf)

    # ---- skills book (file-backed, two roots: bundle + operator) ---------
    skills_book = build_default_skills_book(_workspace_dir)

    # ---- stream hub (in-process pipe registry) ------------------------------
    from old_bus.bases.stream import StreamHub

    stream_hub = StreamHub()

    # ---- local job boards ---------------------------------------------------
    agent_job_board = chatNotifyBoard(
        local_factory,
        contact_book=contacts_book,
        messages_book=messages_book,
        conversations_book=conversations_book,
    )
    tool_job_board = runToolJobBoard(local_factory)
    llm_job_board = callLLMJobBoard(local_factory)
    delivery_notify_job_board = deliveryNotifyJobBoard(local_factory, messages_book=messages_book)
    change_provider_config_job_board = changeProviderConfigJobBoard(
        local_factory, settings_book=settings_book
    )
    change_mcp_server_job_board = changeMCPServerJobBoard(local_factory)
    seed_preset_task_job_board = seedPresetTaskJobBoard(local_factory)
    run_task_job_board = runTaskJobBoard(local_factory)

    # ---- magis_book books -------------------------------------------------------
    if magis_factory is not None:
        magis_book = MagisBook(magis_factory)
        magis_admins_book = MagisAdminBook(magis_factory)
        control_settings_book = ControlSettingBook(magis_factory)
        # ``MagisMembershipBook.instruction_context`` reads the per-MAGI
        # personal instruction from the local SettingBook (agent-worker-
        # bus.md §6).  A singleton control process has no local MAGI
        # settings, so it must not be given the MAGIS control KV as a
        # substitute.
        memberships_book = MagisMembershipBook(
            magis_factory,
            settings_book=settings_book,
        )
        roles_book = MagisRoleBook(magis_factory)
        runtime_state_book = RuntimeBook(magis_factory)
        control_secrets_book = ControlSecretBook(magis_factory)
        a2a_request_job_board = a2aRequestJobBoard(
            magis_factory,
            memberships_book=memberships_book,
            messages_book=messages_book,
            conversations_book=conversations_book,
        )
        a2a_notify_job_board = a2aNotifyBoard(
            magis_factory,
            memberships_book=memberships_book,
            messages_book=messages_book,
            conversations_book=conversations_book,
        )
    else:
        magis_book = None
        magis_admins_book = None
        memberships_book = None
        roles_book = None
        runtime_state_book = None
        control_secrets_book = None
        control_settings_book = None
        a2a_request_job_board = None
        a2a_notify_job_board = None

    # A2A is a BUS capability, rather than a channel-worker capability.  Its
    # option therefore belongs to BUS bootstrap; delivery/scheduler workers
    # register their own names when the runtime starts.
    settings_book.register_channel(name="a2a")

    # ---- assemble ----------------------------------------------------------
    return Bus(
        conversations_book=conversations_book,
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
        change_mcp_server_job_board=change_mcp_server_job_board,
        tool_job_board=tool_job_board,
        agent_job_board=agent_job_board,
        llm_job_board=llm_job_board,
        delivery_notify_job_board=delivery_notify_job_board,
        a2a_request_job_board=a2a_request_job_board,
        a2a_notify_job_board=a2a_notify_job_board,
        change_provider_config_job_board=change_provider_config_job_board,
        seed_preset_task_job_board=seed_preset_task_job_board,
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
        runtime_state_book=runtime_state_book,
        control_secrets_book=control_secrets_book,
        control_settings_book=control_settings_book,
        _local_factory=local_factory,
        _magis_factory=magis_factory,
    )
