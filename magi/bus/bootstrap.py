"""Composition-root opening for BUS.

Provides :func:`open_bus` — a pure function that opens local
SQLite, MAGIS database, and file-storage access into a single
:class:`Bus` facade.  All paths are passed explicitly; no
environment variable reads, no auto-discovery.  The composition root
(:mod:`magi.startup.runtime`) calls this after resolving identity and
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
from typing import TYPE_CHECKING

from magi.bus.db.engine import EngineFactory, build_local_factory, build_magis_factory

if TYPE_CHECKING:
    from magi.bus.guild.callLLMJob import callLLMJobBoard
    from magi.bus.guild.changeProviderConfigJob import changeProviderConfigJobBoard
    from magi.bus.guild.chatJob import chatJobBoard
    from magi.bus.guild.deliveryJob import deliveryJobBoard
    from magi.bus.guild.mcpServerChangedJob import mcpServerChangedJobBoard
    from magi.bus.guild.runTaskJob import runTaskJobBoard
    from magi.bus.guild.runToolJob import runToolJobBoard
    from magi.bus.guild.seedPresetTasksJob import seedPresetTasksJobBoard
    from magi.bus.guild.sendA2AJob import sendA2AJobBoard
    from magi.bus.library.file.promptBook import PromptBook
    from magi.bus.library.file.skillsBook import SkillsBook
    from magi.bus.library.local.actionItemBook import ActionItemBook
    from magi.bus.library.local.contactBook import ContactBook, ContactNoteBook
    from magi.bus.library.local.hookSignoffBook import HookSignoffBook
    from magi.bus.library.local.mcpServerBook import McpServerBook
    from magi.bus.library.local.memoryBook import MemoryBook
    from magi.bus.library.local.conversationBook import (
        ConversationBook,
        MessageBook,
    )
    from magi.bus.library.local.settingBook import SettingBook
    from magi.bus.library.local.tasksBook import TaskBook, TaskRunBook
    from magi.bus.library.local.tokenUsageBook import TokenUsageBook
    from magi.bus.library.local.toolsBook import (
        ToolCatalogStateBook,
        ToolDefinitionBook,
    )
    from magi.bus.library.magis.authCredentialBook import AuthCredentialBook
    from magi.bus.library.magis.magisBook import MagisAdminBook, MagisBook
    from magi.bus.library.magis.membershipBook import (
        MagisMembershipBook,
        MagisRoleBook,
    )
    from magi.bus.library.magis.runtimeBook import (
        ControlSecretBook,
        RuntimeBook,
    )
    from magi.bus.stream import StreamHub

logger = logging.getLogger("magi.bus.bootstrap")


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

        bus = open_bus(state_dir="...", magis_url="...")
        job = bus.tool_job_board.claim()
        adam = bus.memberships_book.get(magi_id=1)  # ADAM = membership id=1

    When MAGIS is not configured, all magis_book-related fields are
    ``None``.
    """

    # -- local: sessions_book (Books) ---------------------------------------------
    # sessions_book is an instance of ConversationBook (SessionBook = alias).

    sessions_book: ConversationBook  # ConversationBook
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
    # (``Task.source == SOURCE_USER``) and preset templates
    # (``Task.source == SOURCE_PROACTIVE``); the old separate
    # ``task_presets_book`` field has been folded into this
    # single Book (parallel to the ``action_items`` refactor).

    tasks_book: TaskBook  # TaskBook
    task_runs_book: TaskRunBook  # TaskRunBook

    # -- local: tools & MCP (Books + Job board) ------------------------------

    tool_definitions_book: ToolDefinitionBook  # ToolDefinitionBook
    tool_catalog_book: ToolCatalogStateBook  # ToolCatalogStateBook
    mcp_servers_book: McpServerBook  # McpServerBook
    mcp_server_changed_job_board: mcpServerChangedJobBoard  # mcpServerChangedJobBoard
    tool_job_board: runToolJobBoard  # runToolJobBoard

    # -- local: agent (Job board) ---------------------------------------------

    agent_job_board: chatJobBoard  # chatJobBoard

    # -- local: LLM (Job board) ----------------------------------------------

    llm_job_board: callLLMJobBoard  # callLLMJobBoard

    # -- local: delivery & A2A (Job boards) ----------------------------------

    delivery_job_board: deliveryJobBoard  # deliveryJobBoard
    a2a_job_board: sendA2AJobBoard  # sendA2AJobBoard

    # -- local: provider config (Job board) ----------------------------------

    change_provider_config_job_board: changeProviderConfigJobBoard  # changeProviderConfigJobBoard

    # -- local: streaming ---------------------------------------------------

    stream_hub: StreamHub  # StreamHub

    # -- local: proactive (Job board) ---------------------------------------

    seed_preset_tasks_job_board: seedPresetTasksJobBoard  # seedPresetTasksJobBoard

    # -- local: task trigger (Job board) -----------------------------------

    run_task_job_board: runTaskJobBoard  # runTaskJobBoard

    # -- local: misc (Books) -------------------------------------------------

    token_usage_book: TokenUsageBook  # TokenUsageBook
    action_items_book: ActionItemBook  # ActionItemBook
    hook_signoffs_book: HookSignoffBook  # HookSignoffBook

    # -- local: prompts (File-backed Book) ----------------------------------
    # Always populated — see bootstrap._resolve_prompts_dir.

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

    # -- magis_book: auth (Book) --------------------------------------------------

    auth_credentials_book: AuthCredentialBook | None = None  # AuthCredentialBook | None


# ---------------------------------------------------------------------------
# public open entry point
# ---------------------------------------------------------------------------


def open_bus(
    *,
    state_dir: str,
    magis_url: str | None = None,
    prompts_dir: str | None = None,
) -> Bus:
    """Open a provisioned ``Bus`` with resolved paths.

    Called by the composition root (e.g. :mod:`magi.startup.runtime`)
    after identity + database paths have been resolved.  Does NOT
    read environment variables or call auto-discovery — all paths
    are passed explicitly.

    If *prompts_dir* is ``None``, the bundled ``magi/prompts/``
    directory is auto-detected from the package location.

    Storage provisioning is intentionally a separate operation in
    :mod:`magi.bus.provision`.  This function never creates directories,
    tables, default settings, or workspace files.  It raises
    :class:`magi.bus.provision.StorageNotProvisioned` when either requested
    database has not been provisioned.

    Returns a ready-to-use ``Bus``.  The caller is responsible for
    passing it to workers via constructor injection.  There is no
    process-level singleton — every component receives its ``Bus``
    explicitly.
    """
    return _open_with_dirs(
        state_dir=state_dir, magis_url=magis_url, prompts_dir=prompts_dir,
    )


def open_control_bus(*, control_dir: str, magis_url: str) -> Bus:
    """Open the singleton control-plane BUS without a node-private store.

    The control plane persists its small operator-facing state in the already
    provisioned MAGIS store.  ``control_dir`` is only the read-only file-book
    root; it is never created here.  In particular, this function must not be
    given a ``MAGI_Citizens/<name>/memories`` path.
    """
    if not magis_url:
        raise ValueError("control plane requires a MAGIS database URL")
    return _open_with_dirs(
        state_dir=control_dir,
        magis_url=magis_url,
        local_database_url=magis_url,
        local_provision_scope="magis",
    )


def _open_with_dirs(
    *,
    state_dir: str,
    magis_url: str | None = None,
    prompts_dir: str | None = None,
    allow_unprovisioned: bool = False,
    local_database_url: str | None = None,
    local_provision_scope: str = "node",
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
        ConversationBook,
        SettingBook,
        TaskBook,
        TaskRunBook,
        TokenUsageBook,
        ToolCatalogStateBook,
        ToolDefinitionBook,
    )
    from magi.bus.library.magis import (
        AuthCredentialBook,
        ControlSecretBook,
        MagisAdminBook,
        MagisBook,
        MagisMembershipBook,
        MagisRoleBook,
        RuntimeBook,
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
    state_path = Path(state_dir)
    if not allow_unprovisioned and local_database_url is None:
        database_path = state_path / "magi.db"
        if not database_path.is_file():
            from magi.bus.provision import StorageNotProvisioned

            raise StorageNotProvisioned(
                f"node database is missing at {database_path}; run the explicit provisioning command"
            )
    local_factory = (
        EngineFactory(local_database_url)
        if local_database_url is not None
        else build_local_factory(state_dir)
    )

    # Pure pass-through: caller is the composition root and owns path
    # resolution.  No env reads — ``magis_url=None`` simply means
    # "no MAGIS database configured" (test / single-MAGIS scenarios).
    magis_factory = build_magis_factory(magis_url) if magis_url else None
    if not allow_unprovisioned:
        from magi.bus.provision import require_provisioned

        require_provisioned(local_factory, scope=local_provision_scope)
        if magis_factory is not None:
            require_provisioned(magis_factory, scope="magis")

    # ---- local books -------------------------------------------------------
    conversations_book = ConversationBook(local_factory)
    messages_book = MessageBook(local_factory)
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
    _workspace_dir = Path(state_dir).parent
    _prompts_dir = _resolve_prompts_dir(prompts_dir)
    prompt_shelf = FileShelf(_prompts_dir, create_root=False)
    prompt_book = PromptBook(prompt_shelf, workspace_dir=_workspace_dir)

    # ---- skills book (file-backed, two roots: bundle + operator) ---------
    skills_book = build_default_skills_book(_workspace_dir)

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
        runtime_state_book = RuntimeBook(magis_factory)
        control_secrets_book = ControlSecretBook(magis_factory)
        auth_credentials_book = AuthCredentialBook(magis_factory)
    else:
        magis_book = None
        magis_admins_book = None
        memberships_book = None
        roles_book = None
        runtime_state_book = None
        control_secrets_book = None
        auth_credentials_book = None

    # ---- assemble ----------------------------------------------------------
    return Bus(
        sessions_book=conversations_book,
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
        runtime_state_book=runtime_state_book,
        control_secrets_book=control_secrets_book,
        auth_credentials_book=auth_credentials_book,
        _local_factory=local_factory,
        _magis_factory=magis_factory,
    )


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _resolve_prompts_dir(prompts_dir: str | None) -> Path:
    """Resolve the bundled prompts directory.

    If *prompts_dir* is explicitly provided, use it as-is.
    Otherwise auto-detect from the ``magi`` package location.
    Raises :class:`RuntimeError` if auto-detection fails — the
    ``Bus.prompt_book`` invariant is "always populated", so a
    missing prompts bundle is a startup failure the operator must
    see, not a silently-degraded runtime state.
    """
    if prompts_dir is not None:
        return Path(prompts_dir)

    # Auto-detect: ``magi/prompts/`` relative to the magi package root.
    # We check for ``soul.md`` (the one file every valid prompts bundle
    # must contain) instead of just ``is_dir()`` to avoid picking up
    # empty or bogus directories on ``sys.path``.
    try:
        import magi
        candidate = Path(magi.__file__).resolve().parent / "prompts"
        if candidate.is_dir() and (candidate / "soul.md").exists():
            return candidate
    except Exception:
        pass

    # Last resort: try relative to *this* file (``bus/bootstrap.py``).
    # ``magi/bus/bootstrap.py`` → ``magi/`` → ``magi/prompts/``
    try:
        candidate = Path(__file__).resolve().parent.parent / "prompts"
        if candidate.is_dir() and (candidate / "soul.md").exists():
            return candidate
    except Exception:
        pass

    raise RuntimeError(
        "Could not locate the MAGI prompts bundle. Pass `prompts_dir=` "
        "explicitly to bootstrap_bus(), or ensure the package is installed "
        "with its bundled `magi/prompts/` directory intact "
        "(expected to contain at least `soul.md`)."
    )


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
