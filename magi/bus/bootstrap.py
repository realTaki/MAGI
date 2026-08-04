"""Composition-root bootstrap for MAGI's local BUS."""

from __future__ import annotations

from dataclasses import dataclass

from magi.bus.services import (
    ActionItemService,
    AgentRunsService,
    AuthService,
    ContactsService,
    ConnectorService,
    DeliveryService,
    MagicService,
    MagisService,
    MemoryService,
    McpService,
    SessionService,
    SettingsService,
    TaskService,
    TaskSchedulerBridge,
    TokenUsageService,
    ToolCatalogService,
    ToolJobsService,
)
from magi.bus.services.dispatcher import DispatcherService
from magi.bus.services.runtime import BackendDispatcherService, RuntimeRegistryService
from magi.bus.store import BusStore

@dataclass(frozen=True, slots=True)
class Bus:
    """Public, domain-partitioned BUS facade for one runtime state directory."""

    agent_runs: AgentRunsService
    tool_catalog: ToolCatalogService
    tool_jobs: ToolJobsService
    delivery: DeliveryService
    settings: SettingsService
    contacts: ContactsService
    connectors: ConnectorService
    session: SessionService
    memory: MemoryService
    mcp: McpService
    task: TaskService
    task_scheduler: TaskSchedulerBridge
    action_item: ActionItemService
    auth: AuthService
    magic: MagicService
    magis: MagisService
    token_usage: TokenUsageService
    dispatcher: DispatcherService
    # Phase 2 — platform-neutral Runtime lifecycle + registry.
    # Appended at the end so the dataclass field order is preserved
    # for callers that rely on positional construction.
    runtime: BackendDispatcherService
    registry: RuntimeRegistryService


def bootstrap(
    state_dir: str,
    *,
    initialise_local: bool = False,
    runtime_backend: object | None = None,
    magis_engine: object | None = None,
) -> Bus:
    """Create the public BUS facade after optionally initialising SQLite.

    ``runtime_backend`` (Phase 2) overrides the backend selected by the
    ``MAGI_BACKEND`` env var — used by tests to inject a stub
    :class:`~magi.orchestrator.backends.base.RuntimeBackend`.

    ``magis_engine`` (Phase 3) lets the Composition Root inject a
    dedicated MAGIS engine (Local Profile SQLite, K8s PostgreSQL, …)
    so the public schema lives outside the Adam's private database.
    """
    if magis_engine is not None:
        # Phase 3 — propagate injected engine to the module-level cache
        # that ``magi.bus.db.magis.engine`` reads on demand.
        from magi.bus.db.magis import engine as _magis_engine_mod

        _magis_engine_mod.set_injected_magis_engine(magis_engine)
    if initialise_local:
        from magi.bus.db import init_orm, init_sqlite

        init_sqlite(state_dir)
        init_orm(state_dir, seed_root=False)
    store = BusStore(state_dir)
    runtime_service = BackendDispatcherService(backend=runtime_backend)
    return Bus(
        agent_runs=AgentRunsService(store),
        tool_catalog=ToolCatalogService(state_dir),
        tool_jobs=ToolJobsService(store),
        delivery=DeliveryService(store),
        settings=SettingsService(state_dir),
        contacts=ContactsService(state_dir),
        connectors=ConnectorService(state_dir),
        session=SessionService(state_dir),
        memory=MemoryService(state_dir),
        mcp=McpService(state_dir),
        task=TaskService(state_dir),
        task_scheduler=TaskSchedulerBridge(state_dir),
        action_item=ActionItemService(state_dir),
        auth=AuthService(state_dir),
        magic=MagicService(state_dir, runtime_dispatcher=runtime_service),
        magis=MagisService(),
        token_usage=TokenUsageService(state_dir),
        dispatcher=DispatcherService(state_dir),
        runtime=runtime_service,
        registry=RuntimeRegistryService(dispatcher=runtime_service),
    )