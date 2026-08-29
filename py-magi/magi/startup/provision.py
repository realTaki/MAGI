"""Explicit MAGI topology and node provisioning commands."""

from __future__ import annotations

from dataclasses import replace

from magi.old_bus.provision import provision_node_storage
from magi.startup.config import DEFAULT_MAGI_NAME, RUNTIME_PORT, ConfigurationError, StartupConfig
from magi.startup.paths import (
    resolve_magis_database_path,
    resolve_magis_database_url,
)
from magi.startup.spec import RuntimeSpec


def _ensure_first_magi_identity(factory, *, magis_name: str) -> int:
    """Create one MAGIS root and its sole ADAM membership if absent."""
    from magi.old_bus.firmwares.books.magis import (
        DEFAULT_ROLE_INSTRUCTIONS,
        Magis,
        MagisBook,
        MagisMembership,
        MagisMembershipBook,
        MagisRole,
        MagisRoleBook,
    )

    magis = MagisBook(factory)
    roles = MagisRoleBook(factory)
    memberships = MagisMembershipBook(factory)
    root_name = "Genesis" if magis_name == "genesis" else magis_name
    root = magis.get_root()
    if root is None:
        root_id = magis.add(Magis(name=root_name))
        root = magis.get(root_id)
        if root is None:
            raise RuntimeError(f"MAGIS row {root_id} disappeared after insert")
    adam_role = roles.find(magis_id=root.id, name="ADAM")
    if adam_role is None:
        role_id = roles.add(MagisRole(
            magis_id=root.id,
            name="ADAM",
            instruction=DEFAULT_ROLE_INSTRUCTIONS["ADAM"],
            is_reserved=True,
        ))
        adam_role = roles.get(role_id)
        if adam_role is None:
            raise RuntimeError(f"ADAM role row {role_id} disappeared after insert")
    member = next(
        (
            item
            for item in memberships.list_for_magis(magis_id=root.id)
            if item.role_id == adam_role.id
        ),
        None,
    )
    if member is None:
        member_id = memberships.add(MagisMembership(magis_id=root.id, role_id=adam_role.id))
        member = memberships.get(member_id)
        if member is None:
            raise RuntimeError(f"ADAM membership row {member_id} disappeared after insert")
    magis.set_adam(magis_id=root.id, adam_id=member.id)
    return member.id


def _ensure_default_admin(*, bus, magi_id: int) -> int:
    """Create the first MAGIS admin and its local Contact projection.

    The first admin is MAGIS-scoped.  A local Contact represents a person a
    MAGI serves, not an operator's authority, so bootstrap must never create
    an ``assigned`` Contact merely to make WebUI login work.
    """

    membership = bus.memberships_book.get(magi_id) if bus.memberships_book else None
    if membership is None or bus.magis_admins_book is None:
        raise RuntimeError("MAGIS admin registry unavailable")
    from magi.old_bus.firmwares.books.magis import MagisAdmin

    grants = bus.magis_admins_book.list_for_magis(magis_id=membership.magis_id)
    existing = next((admin for admin in grants if admin.name == "admin"), None)
    if existing is None:
        admin_id = bus.magis_admins_book.add(
            MagisAdmin(name="admin", magis_id=membership.magis_id)
        )
        existing = bus.magis_admins_book.get(admin_id)
        if existing is None:
            raise RuntimeError(f"MAGIS admin row {admin_id} disappeared after insert")
    projection = bus.contacts_book.get_by_magis_admin_id(magis_admin_id=existing.id)
    if projection is None:
        # This is not an assigned user.  It merely anchors the MAGIS admin's
        # local conversations and ActionItems in eva-000.
        bus.contacts_book.ensure_magis_admin_projection(
            magis_admin_id=existing.id,
            display_name=existing.name,
        )
    return existing.id


def _ensure_control_secret(*, bus, magis_name: str) -> str:
    """Provision the MAGIS control secret in the shared database."""
    import hashlib
    import secrets

    book = bus.control_secrets_book
    if book is None:
        raise RuntimeError("MAGIS control_secrets_book unavailable; cannot seed secret")

    existing = book.get_by_name(name=magis_name)
    if existing is not None and existing.secret_value:
        return existing.secret_value.decode("utf-8")

    value = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16)
    secret_hash = hashlib.sha256(value.encode("utf-8") + salt).digest()
    book.upsert(
        name=magis_name,
        secret_hash=secret_hash,
        salt=salt,
        secret_value=value.encode("utf-8"),
    )
    return value


def _register_local_runtime(*, bus, runtime_id: int, config: StartupConfig, port: int) -> None:
    runtimes = bus.runtime_state_book
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
    """Provision the first MAGI and the selected MAGIS shared store."""
    if config.magi_name != DEFAULT_MAGI_NAME:
        raise ConfigurationError("`magi init` only provisions the first eva-000 MAGI")
    config.host_workspace_dir.mkdir(parents=True, exist_ok=True)
    for name in ("logs", "run"):
        (config.host_workspace_dir / name).mkdir(parents=True, exist_ok=True)
    config.validate()
    magis_url = config.magis_database_url or resolve_magis_database_url(
        config.host_workspace_dir, config.magis_name
    )
    if config.magis_database_url is None:
        resolve_magis_database_path(config.host_workspace_dir, config.magis_name).parent.mkdir(
            parents=True, exist_ok=True
        )
    bus = provision_node_storage(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=magis_url,
    )
    if bus._magis_factory is None:
        raise RuntimeError("MAGIS store was not provisioned")
    magi_id = _ensure_first_magi_identity(bus._magis_factory, magis_name=config.magis_name)
    _ensure_default_admin(bus=bus, magi_id=magi_id)
    _register_local_runtime(bus=bus, runtime_id=magi_id, config=config, port=RUNTIME_PORT)
    if bus.runtime_state_book is None:
        raise RuntimeError("MAGIS port allocation service unavailable")
    existing_state = bus.runtime_state_book.get_by_runtime_id(runtime_id=magi_id)
    if existing_state is None or existing_state.port_in_use_since is None:
        bus.runtime_state_book.allocate_port(runtime_id=magi_id, port=RUNTIME_PORT)
    _ensure_control_secret(bus=bus, magis_name=config.magis_name)
    spec = RuntimeSpec(
        magi_name=DEFAULT_MAGI_NAME,
        magi_id=str(magi_id),
        magis_name=config.magis_name,
        magis_database_url=magis_url,
        runtime_port=RUNTIME_PORT,
        is_first_magi=True,
    )
    # Identity now lives in the MAGIS runtime_state / magis tables; the
    # ``RuntimeSpec`` returned here is reconstructed on demand by
    # :func:`magi.startup.spec.load_runtime_spec` from those rows.
    return spec


def create_node(config: StartupConfig) -> RuntimeSpec:
    """Register and provision one EVA under an already initialised MAGIS."""
    if config.magi_name == DEFAULT_MAGI_NAME:
        raise ConfigurationError("eva-000 is created only by `magi init`")
    if config.workspace_dir.exists():
        raise ConfigurationError(
            f"node workspace already exists at {config.workspace_dir}; clean the state before creating it again"
        )
    config.validate()
    magis_url = config.magis_database_url or resolve_magis_database_url(
        config.host_workspace_dir, config.magis_name
    )
    from magi.old_bus import open_bus
    from magi.old_bus.firmwares.books.magis import (
        MagisBook,
        MagisMembership,
        MagisMembershipBook,
        MagisRole,
        MagisRoleBook,
    )

    node_config = replace(config, magis_database_url=magis_url)
    control_bus = open_bus(magis_url=magis_url)
    factory = control_bus._magis_factory
    magis = MagisBook(factory)
    roles = MagisRoleBook(factory)
    memberships = MagisMembershipBook(factory)
    root = magis.get_root()
    if root is None:
        raise ConfigurationError(
            f"MAGIS {config.magis_name!r} is not provisioned; run `magi init` first"
        )
    eva_role = roles.find(magis_id=root.id, name="EVA")
    if eva_role is None:
        eva_role_id = roles.add(MagisRole(magis_id=root.id, name="EVA", is_reserved=True))
        eva_role = roles.get(eva_role_id)
        if eva_role is None:
            raise RuntimeError(f"EVA role row {eva_role_id} disappeared after insert")

    if control_bus.runtime_state_book is None:
        raise RuntimeError("MAGIS port allocation service unavailable")
    used = control_bus.runtime_state_book.list_allocated_ports()
    port = next(
        (
            candidate
            for candidate in range(RUNTIME_PORT + 1, RUNTIME_PORT + 100)
            if candidate not in used
        ),
        None,
    )
    if port is None:
        raise ConfigurationError("no local runtime port is available")

    # Materialise the new node only after all existing control-plane state is
    # known valid, and before allocating a membership/port.  A rejected legacy
    # node path therefore cannot leave an orphaned registry record.
    provision_node_storage(
        state_dir=str(node_config.workspace_dir / "memories"),
        magis_url=magis_url,
    )
    membership_id = memberships.add(MagisMembership(magis_id=root.id, role_id=eva_role.id))
    membership = memberships.get(membership_id)
    if membership is None:
        raise RuntimeError(f"membership row {membership_id} disappeared after insert")
    _register_local_runtime(
        bus=control_bus,
        runtime_id=membership.id,
        config=config,
        port=port,
    )
    control_bus.runtime_state_book.allocate_port(runtime_id=membership.id, port=port)

    node_config = replace(node_config, magi_id=str(membership.id))
    spec = RuntimeSpec(
        magi_name=node_config.magi_name,
        magi_id=str(membership.id),
        magis_name=node_config.magis_name,
        magis_database_url=magis_url,
        runtime_port=port,
        is_first_magi=False,
    )
    # Identity is durable in the MAGIS runtime_state / magis rows; the
    # returned ``RuntimeSpec`` is reconstructed on demand from those by
    # :func:`magi.startup.spec.load_runtime_spec`.
    return spec


__all__ = ["create_node", "init_first_magi"]
