"""Explicit MAGI topology and node provisioning commands."""

from __future__ import annotations

from dataclasses import replace

from magi.bus.provision import provision_node_storage
from magi.startup.config import DEFAULT_MAGI_NAME, ConfigurationError, RUNTIME_PORT, StartupConfig
from magi.startup.paths import resolve_magis_database_path, resolve_magis_database_url
from magi.startup.spec import RuntimeSpec, write_runtime_spec


def _ensure_first_magi_identity(factory) -> int:
    """Create Genesis and its sole ADAM membership if absent."""
    from magi.bus.library.magis import (
        DEFAULT_ROLE_INSTRUCTIONS,
        MagisBook,
        MagisMembershipBook,
        MagisRoleBook,
    )

    magis = MagisBook(factory)
    roles = MagisRoleBook(factory)
    memberships = MagisMembershipBook(factory)
    genesis = magis.get_root() or magis.add(name="Genesis")
    adam_role = roles.find(magis_id=genesis.id, name="ADAM")
    if adam_role is None:
        adam_role = roles.add(
            magis_id=genesis.id,
            name="ADAM",
            instruction=DEFAULT_ROLE_INSTRUCTIONS["ADAM"],
            is_reserved=True,
        )
    member = next(
        (item for item in memberships.list_for_magis(magis_id=genesis.id) if item.role_id == adam_role.id),
        None,
    )
    if member is None:
        member = memberships.add(magis_id=genesis.id, role_id=adam_role.id)
    magis.set_adam(magis_id=genesis.id, adam_id=member.id)
    return member.id


def _ensure_control_secret(path) -> str:
    import os
    import secrets

    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return value


def _register_local_runtime(*, bus, runtime_id: int, config: StartupConfig, port: int) -> None:
    runtimes = bus.control_runtimes_book
    if runtimes is None:
        raise RuntimeError("MAGIS runtime registry unavailable")
    workspace = config.workspace_dir
    runtimes.upsert(
        runtime_id=runtime_id,
        backend_kind="local",
        backend_ref=config.magi_name,
        workspace_dir=str(workspace),
        log_dir=str(workspace / "logs"),
        audit_log_path=str(workspace / "logs" / "audit.log"),
        port=port,
        base_url=f"http://127.0.0.1:{port}",
    )


def init_first_magi(config: StartupConfig) -> RuntimeSpec:
    """Provision the only allowed first node and its Genesis topology."""
    if config.magi_name != DEFAULT_MAGI_NAME:
        raise ConfigurationError("`magi init` only provisions the first eva-000 MAGI")
    config.host_workspace_dir.mkdir(parents=True, exist_ok=True)
    for name in ("logs", "run"):
        (config.host_workspace_dir / name).mkdir(parents=True, exist_ok=True)
    magis_url = config.magis_database_url or resolve_magis_database_url(config.host_workspace_dir)
    if config.magis_database_url is None:
        resolve_magis_database_path(config.host_workspace_dir).parent.mkdir(parents=True, exist_ok=True)
    bus = provision_node_storage(
        state_dir=str(config.workspace_dir / "memories"), magis_url=magis_url,
    )
    if bus._magis_factory is None:
        raise RuntimeError("MAGIS store was not provisioned")
    magi_id = _ensure_first_magi_identity(bus._magis_factory)
    _register_local_runtime(bus=bus, runtime_id=magi_id, config=config, port=RUNTIME_PORT)
    if bus.port_allocations_book is None:
        raise RuntimeError("MAGIS port allocation service unavailable")
    if bus.port_allocations_book.get(runtime_id=magi_id) is None:
        bus.port_allocations_book.allocate(runtime_id=magi_id, port=RUNTIME_PORT)
    _ensure_control_secret(resolve_magis_database_path(config.host_workspace_dir).parent / "control-secret")
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
    if config.workspace_dir.exists():
        raise ConfigurationError(
            f"node workspace already exists at {config.workspace_dir}; clean the state before creating it again"
        )
    magis_url = config.magis_database_url or resolve_magis_database_url(config.host_workspace_dir)
    from magi.bus import open_control_bus
    from magi.bus.library.magis import MagisBook, MagisMembershipBook, MagisRoleBook

    node_config = replace(config, magis_database_url=magis_url)
    control_bus = open_control_bus(
        control_dir=str(config.host_workspace_dir / "MAGI_Societies" / "genesis" / "control"),
        magis_url=magis_url,
    )
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

    ports = control_bus.port_allocations_book
    if ports is None:
        raise RuntimeError("MAGIS port allocation service unavailable")
    used = {item.port for item in ports.list_active()}
    port = next((candidate for candidate in range(RUNTIME_PORT + 1, RUNTIME_PORT + 100) if candidate not in used), None)
    if port is None:
        raise ConfigurationError("no local runtime port is available")

    # Materialise the new node only after all existing control-plane state is
    # known valid, and before allocating a membership/port.  A rejected legacy
    # node path therefore cannot leave an orphaned registry record.
    provision_node_storage(
        state_dir=str(node_config.workspace_dir / "memories"), magis_url=magis_url,
    )
    membership = memberships.add(magis_id=genesis.id, role_id=eva_role.id)
    _register_local_runtime(
        bus=control_bus, runtime_id=membership.id, config=config, port=port,
    )
    ports.allocate(runtime_id=membership.id, port=port)

    node_config = replace(node_config, magi_id=str(membership.id))
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
