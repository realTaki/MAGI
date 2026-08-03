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
    TokenUsageService,
    ToolCatalogService,
    ToolJobsService,
)
from magi.bus.services.dispatcher import DispatcherService
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
    action_item: ActionItemService
    auth: AuthService
    magic: MagicService
    magis: MagisService
    token_usage: TokenUsageService
    dispatcher: DispatcherService


def bootstrap(state_dir: str, *, initialise_local: bool = False) -> Bus:
    """Create the public BUS facade after optionally initialising SQLite."""
    if initialise_local:
        from magi.bus._persistence import init_orm, init_sqlite

        init_sqlite(state_dir)
        init_orm(state_dir, seed_root=False)
    store = BusStore(state_dir)
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
        action_item=ActionItemService(state_dir),
        auth=AuthService(state_dir),
        magic=MagicService(state_dir),
        magis=MagisService(),
        token_usage=TokenUsageService(state_dir),
        dispatcher=DispatcherService(state_dir),
    )
