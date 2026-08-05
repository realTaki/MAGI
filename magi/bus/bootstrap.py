"""Composition-root bootstrap for MAGI's local BUS."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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
from magi.bus.services.control_registry import ControlRegistryService
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
    # Phase 3 close-out — Local control-plane registry (no-op on K8s
    # where the Compose-Root passes ``control_engine=None``).
    control_registry: Optional[ControlRegistryService] = None


def bootstrap(
    *,
    initialise_local: bool = False,
    runtime_backend: object | None = None,
    magis_engine: object | None = None,
    control_engine: object | None = None,
) -> Bus:
    """Create the public BUS facade for the container / K8s profile.

    The container profile's state directory is derived from
    :func:`magi.launcher.paths.state_dir` (which reads
    ``MAGI_WORKSPACE_DIR``).  The Local Profile, which has its own
    layout, reaches :func:`_bootstrap` directly with the layout's
    ``state_dir``.

    ``runtime_backend`` (Phase 2) overrides the backend selected by the
    ``MAGI_BACKEND`` env var — used by tests to inject a stub
    :class:`~magi.orchestrator.backends.base.RuntimeBackend`.

    ``magis_engine`` (Phase 3) lets the Composition Root inject a
    dedicated MAGIS engine (Local Profile SQLite, K8s PostgreSQL, …)
    so the public schema lives outside the Adam's private database.

    ``control_engine`` (Phase 3 close-out) lets the Composition Root
    inject the Local control-plane registry engine.  K8s Profile
    passes ``None``; the resulting Bus still exposes a
    ``control_registry`` slot — it just resolves to ``None``.
    """
    from magi.launcher.paths import state_dir as _state_dir

    return _bootstrap(
        str(_state_dir()),
        initialise_local=initialise_local,
        runtime_backend=runtime_backend,
        magis_engine=magis_engine,
        control_engine=control_engine,
    )


def _bootstrap(
    state_dir: str,
    *,
    initialise_local: bool = False,
    runtime_backend: object | None = None,
    magis_engine: object | None = None,
    control_engine: object | None = None,
) -> Bus:
    """Private — current implementation of :func:`bootstrap`.

    Takes ``state_dir`` explicitly.  Called by:

    - :func:`bootstrap` for container / K8s (after reading state_dir()).
    - :func:`magi.launcher.bootstrap_local` for the Local Profile
      (passes ``layout.state_dir``).

    Business modules never call this; they use :func:`get_bus`.
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

    control_registry_service: Optional[ControlRegistryService] = None
    if control_engine is not None:
        from magi.bus.db.control.repository import ControlRepository

        control_registry_service = ControlRegistryService(
            ControlRepository(control_engine)
        )

    bus = Bus(
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
        task_scheduler=TaskSchedulerBridge(),
        action_item=ActionItemService(state_dir),
        auth=AuthService(state_dir),
        magic=MagicService(state_dir, runtime_dispatcher=runtime_service),
        magis=MagisService(),
        token_usage=TokenUsageService(state_dir),
        dispatcher=DispatcherService(state_dir),
        runtime=runtime_service,
        registry=RuntimeRegistryService(dispatcher=runtime_service),
        control_registry=control_registry_service,
    )
    # ``_bootstrap`` is the only place a Bus is constructed. Registering it
    # as the process-wide singleton here lets lazy resolvers (e.g.
    # ``RuntimeService.backend`` falling back to ``get_bus().control_registry``
    # in ``LocalProcessRuntimeBackend.__init__``) see the same
    # control_registry the Composition Root injected — without this, the
    # Local Profile's ``bootstrap_local`` builds a wired Bus but the first
    # ``get_bus()`` call from the backend would construct a fresh one with
    # ``control_registry=None`` and crash on the ``None`` deref.
    global _bus
    _bus = bus
    return bus


# --------------------------------------------------------------------------- #
# process-wide singleton
# --------------------------------------------------------------------------- #

_bus: Bus | None = None


def get_bus() -> Bus:
    """Return the process-wide BUS facade, initialising it on first call.

    This is the **only** entry point that modules outside ``magi.bus``
    should use.  It hides ``state_dir`` entirely —
    consumers receive a ready-to-use ``Bus`` without ever knowing where
    the SQLite database lives.

    The singleton is process-wide (like :func:`magi.bus.db.engine.get_engine`).
    Tests that need a different state directory should call
    :func:`_bootstrap` directly or set ``MAGI_WORKSPACE_DIR`` before the first
    call to :func:`get_bus`.
    """
    global _bus
    if _bus is None:
        _bus = bootstrap()
    return _bus


_BUS_STORE: "BusStore | None" = None


def get_bus_store() -> "BusStore":
    """Return the process-wide :class:`BusStore` (low-level queue ops).

    The store is created on first call alongside the BUS, sharing the
    same ``state_dir``.  Modules that need direct access to the
    durable queue primitives (claim / recover / park) without pulling
    in the full bus facade use this entry point.
    """
    global _BUS_STORE
    if _BUS_STORE is None:
        from magi.launcher.paths import state_dir as _launcher_state_dir
        from magi.bus.store import BusStore as _BusStore

        _BUS_STORE = _BusStore(state_dir=str(_launcher_state_dir()))
    return _BUS_STORE