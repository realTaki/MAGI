"""MAGI bootstrap — first-MAGI vs. join-existing MAGI (plan §11-§13).

Single entry point :func:`bootstrap_magi` inspects
``StartupConfig.magis_database_url`` to decide which sub-flow runs:

- :func:`bootstrap_first_magi` — creates MAGIS database, Genesis
  MAGIS, ``eva-000`` identity, ADAM membership, the first WebUI, etc.
- :func:`bootstrap_existing_magi` — connects to ``MAGIS_DATABASE_URL``
  and validates ``MAGI_ID`` against the MAGIS tree.

Both flows are idempotent. Restarting an existing MAGI never creates a
second WebUI, never re-seeds Genesis, never duplicates ``eva-000`` /
membership / role rows.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magi.startup.config import (
    DEFAULT_MAGI_NAME,
    ConfigurationError,
    StartupConfig,
    StartupContext,
)
from magi.startup.paths import (
    ensure_workspace,
    resolve_magis_database_url,
    resolve_private_database_path,
    resolve_runtime_state_path,
)

logger = logging.getLogger("magi.startup.bootstrap")


def _magis_factory(database_url: str):
    """Build and initialise the NewBus-owned MAGIS schema."""
    from magi.new_bus.db.engine import build_magis_factory
    # Import the Books before ``create_all`` so their inline ORM models are
    # registered on NewBus's independent metadata.
    from magi.new_bus.library.magis import (  # noqa: F401
        AuthCredentialBook,
        ControlRuntimeBook,
        EvaRuntimeBook,
        MagisAdminBook,
        MagisBook,
        MagisMembershipBook,
        MagisRoleBook,
    )

    factory = build_magis_factory(database_url)
    factory.create_all()
    return factory


# ----------------------------------------------------------------------
# Bootstrap result type
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapIdentity:
    """Output of either bootstrap sub-flow — consumed by :func:`bootstrap_magi`."""

    magi_id: str
    magis_database_url: str
    is_first_magi: bool


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def bootstrap_magi(config: StartupConfig) -> StartupContext:
    """Bootstrap a single MAGI from a resolved :class:`StartupConfig`.

    Always idempotent. Returns a :class:`StartupContext` ready to hand
    to the runtime layer.
    """
    config.validate()
    # Plan §11 — ensure workspace dirs before bootstrapping identity.
    workspace_dir = ensure_workspace(config.workspace_dir)

    is_first = config.magis_database_url is None
    if is_first:
        identity = bootstrap_first_magi(config, workspace_dir)
    else:
        identity = bootstrap_existing_magi(config, workspace_dir)

    private_database_url = ensure_private_database(workspace_dir)

    persist_runtime_state(workspace_dir, identity, private_database_url)

    return StartupContext(
        host_workspace_dir=config.host_workspace_dir,
        workspace_dir=workspace_dir,
        magi_name=config.magi_name,
        magi_id=identity.magi_id,
        magis_database_url=identity.magis_database_url,
        private_database_url=private_database_url,
        is_first_magi=identity.is_first_magi,
    )


# ----------------------------------------------------------------------
# First MAGI (eva-000 / Genesis)
# ----------------------------------------------------------------------


def bootstrap_first_magi(
    config: StartupConfig,
    workspace_dir: Path,
) -> BootstrapIdentity:
    """Create the very first MAGIS, Genesis MAGIS, ``eva-000`` identity.

    Per plan §12 + §3.1:

    1. Create MAGIS database (SQLite by default).
    2. Create Genesis MAGIS.
    3. Create ``eva-000`` identity.
    4. Create ADAM Membership.
    5. Initialise ``eva-000``'s private workspace.

    Idempotent — repeated calls do not duplicate rows.
    """
    if config.magi_name != DEFAULT_MAGI_NAME:
        # Plan §12 — the first MAGI is always eva-000.
        raise ConfigurationError(
            f"The first MAGI must be {DEFAULT_MAGI_NAME!r} "
            f"(got {config.magi_name!r})"
        )

    magis_url = resolve_magis_database_url(config.host_workspace_dir)
    # The canonical MAGIS SQLite path is the single source of truth —
    # we reuse :func:`resolve_magis_database_path` rather than parsing
    # the URL string with ad-hoc slicing.
    from magi.startup.paths import resolve_magis_database_path

    magis_db_path = resolve_magis_database_path(config.host_workspace_dir)
    magis_db_path.parent.mkdir(parents=True, exist_ok=True)

    factory = _magis_factory(magis_url)

    # Plan §22.2 — validate workspace identity before seeding.
    _validate_workspace_identity(
        workspace_dir=workspace_dir,
        magis_database_url=magis_url,
        magi_id=str(1),  # First MAGI is always id=1 (Adam).
    )

    # Seed Genesis + the first MAGI (eva-000) + ADAM membership if absent.
    magic_id = _ensure_first_magi_identity(factory)

    logger.info(
        "first MAGI bootstrapped",
        extra={"magi_id": magic_id, "magis_url": magis_url},
    )
    return BootstrapIdentity(
        magi_id=str(magic_id),
        magis_database_url=magis_url,
        is_first_magi=True,
    )


def _ensure_first_magi_identity(factory: Any) -> int:
    """Create Genesis, its ADAM role and first membership if missing.

    NewBus models a MAGI identity as ``magis_memberships.id``.  Display
    name and personal instruction belong to the node's local settings, not
    to a second, global ``magic`` table.
    """
    from magi.new_bus.library.magis import (
        DEFAULT_ROLE_INSTRUCTIONS,
        MagisBook,
        MagisMembershipBook,
        MagisRoleBook,
    )

    magis = MagisBook(factory)
    roles = MagisRoleBook(factory)
    memberships = MagisMembershipBook(factory)
    genesis = magis.get_root()
    if genesis is None:
        genesis = magis.add(name="Genesis")
    adam_role = roles.find(magis_id=genesis.id, name="ADAM")
    if adam_role is None:
        adam_role = roles.add(
            magis_id=genesis.id,
            name="ADAM",
            instruction=DEFAULT_ROLE_INSTRUCTIONS["ADAM"],
            is_reserved=True,
        )
    member = next(
        (item for item in memberships.list_for_magis(magis_id=genesis.id)
         if item.role_id == adam_role.id),
        None,
    )
    if member is None:
        member = memberships.add(magis_id=genesis.id, role_id=adam_role.id)
    magis.set_adam(magis_id=genesis.id, adam_id=member.id)
    return member.id


# ----------------------------------------------------------------------
# Existing MAGI
# ----------------------------------------------------------------------


def bootstrap_existing_magi(
    config: StartupConfig,
    workspace_dir: Path,
) -> BootstrapIdentity:
    """Join an already-bootstrapped MAGIS (plan §3.2 + §13).

    Validates:

    - ``MAGI_ID`` exists in the MAGIS tree
    - Workspace identity matches the persisted sidecar
    - ``MAGI_NAME`` matches the row

    Refuses to:

    - Create a new MAGIS
    - Create a new Genesis
    - Create a second ADAM
    - Auto-register an unknown MAGI ID
    """
    if not config.magis_database_url:
        raise ConfigurationError(
            "bootstrap_existing_magi requires MAGIS_DATABASE_URL"
        )
    if not config.magi_id:
        raise ConfigurationError(
            "MAGI_ID is required when joining an existing MAGIS"
        )

    factory = _magis_factory(config.magis_database_url)
    membership = _load_membership(factory, config.magi_id)
    if membership is None:
        raise ConfigurationError(
            f"MAGI_ID {config.magi_id!r} not found in MAGIS "
            f"({config.magis_database_url})"
        )

    _validate_workspace_identity(
        workspace_dir=workspace_dir,
        magis_database_url=config.magis_database_url,
        magi_id=config.magi_id,
    )

    return BootstrapIdentity(
        magi_id=str(membership.id),
        magis_database_url=config.magis_database_url,
        is_first_magi=False,
    )


def _load_membership(factory: Any, magi_id: str) -> Any | None:
    from magi.new_bus.library.magis import MagisMembershipBook

    try:
        magic_id_int = int(magi_id)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"MAGI_ID must be an integer: {magi_id!r}") from exc

    return MagisMembershipBook(factory).get(magi_id=magic_id_int)


def _validate_workspace_identity(
    *,
    workspace_dir: Path,
    magis_database_url: str,
    magi_id: str,
) -> None:
    """Plan §22 — refuse to overwrite a workspace that points at a different MAGI."""
    sidecar = resolve_runtime_state_path(workspace_dir)
    if not sidecar.exists():
        return
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("could not parse workspace sidecar %s; ignoring", sidecar)
        return
    existing_magi_id = str(payload.get("magi_id") or "")
    existing_url = str(payload.get("magis_database_url") or "")
    if existing_magi_id and existing_magi_id != magi_id:
        raise ConfigurationError(
            f"workspace {workspace_dir} is bound to MAGI_ID={existing_magi_id!r}; "
            f"refusing to start with MAGI_ID={magi_id!r}"
        )
    if existing_url and existing_url != magis_database_url:
        raise ConfigurationError(
            f"workspace {workspace_dir} is bound to MAGIS={existing_url!r}; "
            f"refusing to start with MAGIS={magis_database_url!r}"
        )


# ----------------------------------------------------------------------
# Private database
# ----------------------------------------------------------------------


def ensure_private_database(workspace_dir: Path) -> str:
    """Materialise the per-MAGI private SQLite (idempotent).

    Returns the DSN as a string suitable for SQLAlchemy.
    """
    db_path = resolve_private_database_path(workspace_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    from magi.new_bus.db.engine import EngineFactory
    # NewBus owns local table registration and creation.  The factory is
    # built from the exact private DSN rather than the retired Bus helper.
    EngineFactory(f"sqlite:///{db_path}").create_all()
    from magi.startup.paths import resolve_private_database_url

    return resolve_private_database_url(workspace_dir)


# ----------------------------------------------------------------------
# Runtime state sidecar
# ----------------------------------------------------------------------


def persist_runtime_state(
    workspace_dir: Path,
    identity: BootstrapIdentity,
    private_database_url: str,
) -> None:
    """Write ``runtime.json`` — plan §22 identity sidecar.

    Always idempotent: same content on every call.
    """
    sidecar = resolve_runtime_state_path(workspace_dir)
    payload = {
        "magi_id": identity.magi_id,
        "magis_database_url": identity.magis_database_url,
        "private_database_url": private_database_url,
        "is_first_magi": identity.is_first_magi,
    }
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# Launcher-issued control secret (was :mod:`magi.startup.security`)
# ----------------------------------------------------------------------


def ensure_control_secret(path: Path) -> str:
    """Return the persisted secret; generate a new one if missing.

    ``path`` is the on-disk file (``<magis_home>/control-secret``).
    The file is forced to ``0600`` on POSIX systems.

    The CLI Profile creates a 256-bit URL-safe random secret the first
    time ``magi cli start`` runs, writes it to ``<magis_home>/control-secret``
    with file-mode ``0600``, and the Bus-store mirrors a salted SHA-256
    digest in the MAGIS database.  The raw secret is required by the
    loopback-only control-plane HTTP API (``X-MAGI-Control-Secret`` header).
    """
    import os
    import secrets

    path = Path(path)
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return raw
    new = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return new


def reveal_control_secret(path: Path) -> str:
    """Read the persisted secret for callers that hold the file mode."""
    return Path(path).read_text(encoding="utf-8").strip()


__all__ = [
    "BootstrapIdentity",
    "bootstrap_magi",
    "bootstrap_first_magi",
    "bootstrap_existing_magi",
    "ensure_private_database",
    "persist_runtime_state",
    # security (merged from :mod:`magi.startup.security`)
    "ensure_control_secret",
    "reveal_control_secret",
]
