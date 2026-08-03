"""Composition-root bootstrap for MAGI's local BUS."""

from __future__ import annotations

from dataclasses import dataclass

from magi.bus.services import AgentRunsService, DeliveryService, SettingsService, ToolJobsService
from magi.bus.store import BusStore
from magi.bus.tool_catalog import ToolCatalogService


@dataclass(frozen=True, slots=True)
class Bus:
    """Public, domain-partitioned BUS facade for one runtime state directory."""

    agent_runs: AgentRunsService
    tool_catalog: ToolCatalogService
    tool_jobs: ToolJobsService
    delivery: DeliveryService
    settings: SettingsService


def bootstrap(state_dir: str, *, initialise_local: bool = False) -> Bus:
    """Create the public BUS facade after optionally initialising SQLite."""
    if initialise_local:
        from magi.db import init_orm, init_sqlite

        init_sqlite(state_dir)
        init_orm(state_dir, seed_root=False)
    store = BusStore(state_dir)
    return Bus(
        agent_runs=AgentRunsService(store),
        tool_catalog=ToolCatalogService(state_dir),
        tool_jobs=ToolJobsService(store),
        delivery=DeliveryService(store),
        settings=SettingsService(state_dir),
    )
