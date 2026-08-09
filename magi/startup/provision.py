"""Explicit MAGI topology and node provisioning commands."""

from __future__ import annotations

from dataclasses import replace

from magi.bus.provision import provision_node_storage
from magi.startup.bootstrap import _ensure_first_magi_identity, ensure_control_secret
from magi.startup.config import DEFAULT_MAGI_NAME, ConfigurationError, RUNTIME_PORT, StartupConfig
from magi.startup.paths import resolve_magis_database_path, resolve_magis_database_url
from magi.startup.spec import RuntimeSpec, write_runtime_spec


def init_first_magi(config: StartupConfig) -> RuntimeSpec:
    """Provision the only allowed first node and its Genesis topology."""
    if config.magi_name != DEFAULT_MAGI_NAME or config.magis_database_url is not None:
        raise ConfigurationError("`magi init` only provisions the first eva-000 MAGI")
    magis_url = resolve_magis_database_url(config.host_workspace_dir)
    bus = provision_node_storage(
        state_dir=str(config.workspace_dir / "memories"), magis_url=magis_url,
    )
    if bus._magis_factory is None:
        raise RuntimeError("MAGIS store was not provisioned")
    magi_id = _ensure_first_magi_identity(bus._magis_factory)
    ensure_control_secret(resolve_magis_database_path(config.host_workspace_dir).parent / "control-secret")
    spec = RuntimeSpec(
        magi_name=DEFAULT_MAGI_NAME,
        magi_id=str(magi_id),
        magis_database_url=magis_url,
        runtime_port=RUNTIME_PORT,
        is_first_magi=True,
    )
    write_runtime_spec(config.workspace_dir, spec)
    return spec


def create_node(config: StartupConfig) -> RuntimeSpec:
    """Register and provision one EVA under an already initialised Genesis."""
    if config.magi_name == DEFAULT_MAGI_NAME:
        raise ConfigurationError("eva-000 is created only by `magi init`")
    magis_url = config.magis_database_url or resolve_magis_database_url(config.host_workspace_dir)
    root_state = config.host_workspace_dir / "MAGI_Citizens" / DEFAULT_MAGI_NAME / "memories"
    from magi.bus import open_bus
    from magi.bus.library.magis import MagisBook, MagisMembershipBook, MagisRoleBook

    control_bus = open_bus(state_dir=str(root_state), magis_url=magis_url)
    if control_bus._magis_factory is None:
        raise ConfigurationError("Genesis MAGIS is not provisioned; run `magi init` first")
    factory = control_bus._magis_factory
    magis = MagisBook(factory)
    roles = MagisRoleBook(factory)
    memberships = MagisMembershipBook(factory)
    genesis = magis.get_root()
    if genesis is None:
        raise ConfigurationError("Genesis MAGIS is not provisioned; run `magi init` first")
    eva_role = roles.find(magis_id=genesis.id, name="EVA")
    if eva_role is None:
        eva_role = roles.add(magis_id=genesis.id, name="EVA", is_reserved=True)
    membership = memberships.add(magis_id=genesis.id, role_id=eva_role.id)

    ports = control_bus.port_allocations_book
    if ports is None:
        raise RuntimeError("MAGIS port allocation service unavailable")
    used = {item.port for item in ports.list_active()}
    port = next((candidate for candidate in range(RUNTIME_PORT + 1, RUNTIME_PORT + 100) if candidate not in used), None)
    if port is None:
        raise ConfigurationError("no local runtime port is available")
    ports.allocate(runtime_id=membership.id, port=port)

    node_config = replace(config, magis_database_url=magis_url, magi_id=str(membership.id))
    provision_node_storage(
        state_dir=str(node_config.workspace_dir / "memories"), magis_url=magis_url,
    )
    spec = RuntimeSpec(
        magi_name=node_config.magi_name,
        magi_id=str(membership.id),
        magis_database_url=magis_url,
        runtime_port=port,
        is_first_magi=False,
    )
    write_runtime_spec(node_config.workspace_dir, spec)
    return spec


__all__ = ["create_node", "init_first_magi"]
