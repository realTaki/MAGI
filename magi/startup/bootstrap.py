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

from sqlalchemy import select

from magi.bus.db.magis import init_magis_public_db
from magi.bus.db.magis.engine import (
    get_magis_engine,
    set_injected_magis_engine,
)
from magi.startup.config import (
    DEFAULT_MAGI_NAME,
    ConfigurationError,
    StartupConfig,
)
from magi.startup.context import StartupContext
from magi.startup.paths import (
    ensure_workspace,
    resolve_magis_database_url,
    resolve_private_database_path,
    resolve_runtime_state_path,
)

logger = logging.getLogger("magi.startup.bootstrap")


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

    # Build a per-MAGIS SQLite engine and inject it into the bus so all
    # callers (init_magis_public_db, repositories, services) see the
    # same DSN.
    from magi.bus.db.magis.local_engine import build as build_local_engine

    engine = build_local_engine(magis_db_path.parent)
    set_injected_magis_engine(engine)
    init_magis_public_db(seed_root=True)

    # Plan §22.2 — validate workspace identity before seeding.
    _validate_workspace_identity(
        workspace_dir=workspace_dir,
        magis_database_url=magis_url,
        magi_id=str(1),  # First MAGI is always id=1 (Adam).
    )

    # Seed Genesis + the first MAGI (eva-000) + ADAM membership if absent.
    magic_id = _ensure_first_magi_identity(engine, name=config.magi_name)

    logger.info(
        "first MAGI bootstrapped",
        extra={"magi_id": magic_id, "magis_url": magis_url},
    )
    return BootstrapIdentity(
        magi_id=str(magic_id),
        magis_database_url=magis_url,
        is_first_magi=True,
    )


def _ensure_first_magi_identity(engine: Any, *, name: str) -> int:
    """Create ``eva-000`` row + ADAM membership if missing.

    Idempotent — restarting the bootstrap is a no-op once seeded.
    """
    from magi.bus.db.engine import _seed_default_root

    # _seed_default_root already covers the legacy ``MAGIC`` /
    # ``MAGIS`` / Genesis / Adam sequence used by the runtime.
    _seed_default_root(engine)

    # Locate the seeded MAGIC row by display name; the legacy seed
    # creates Adam as ``id=1`` with display name ``"EVA-000"``.
    from magi.bus.db.models.magis.magic import MAGIC
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        row = session.scalar(select(MAGIC).where(MAGIC.name == name).limit(1))
        if row is None:
            # Fallback — pick the first MAGIC row (legacy seed is ``id=1``).
            row = session.scalar(select(MAGIC).order_by(MAGIC.id).limit(1))
        if row is None:
            raise ConfigurationError(
                "MAGIS seed did not create a MAGIC row — bootstrap incomplete"
            )
        magic_id = int(row.id)

    return magic_id


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

    engine = _resolve_existing_magis_engine(config.magis_database_url)
    init_magis_public_db(seed_root=False)

    magic_row = _load_magic_row(engine, config.magi_id)
    if magic_row is None:
        raise ConfigurationError(
            f"MAGI_ID {config.magi_id!r} not found in MAGIS "
            f"({config.magis_database_url})"
        )

    actual_name = getattr(magic_row, "name", None) or DEFAULT_MAGI_NAME
    if actual_name != config.magi_name:
        raise ConfigurationError(
            f"MAGI_NAME mismatch: env says {config.magi_name!r}, "
            f"MAGIS has {actual_name!r} for MAGI_ID {config.magi_id}"
        )

    _validate_workspace_identity(
        workspace_dir=workspace_dir,
        magis_database_url=config.magis_database_url,
        magi_id=config.magi_id,
    )

    return BootstrapIdentity(
        magi_id=str(magic_row.id),
        magis_database_url=config.magis_database_url,
        is_first_magi=False,
    )


def _resolve_existing_magis_engine(url: str) -> Any:
    """Materialise a MAGIS engine from ``MAGIS_DATABASE_URL``.

    For SQLite the engine is built locally (mirrors
    :func:`bootstrap_first_magi`); for PostgreSQL the URL is forwarded
    to ``get_magis_engine`` which honours the env-var path.
    """
    if url.startswith("sqlite:///"):
        from magi.bus.db.magis.local_engine import build as build_local_engine

        db_path = Path(url[len("sqlite:///"):])
        engine = build_local_engine(db_path.parent)
        set_injected_magis_engine(engine)
        return engine
    # PostgreSQL path — let the standard engine resolver construct it.
    # We don't inject it so the URL drives the connection.
    return get_magis_engine()


def _load_magic_row(engine: Any, magi_id: str) -> Any | None:
    from magi.bus.db.models.magis.magic import MAGIC
    from sqlalchemy.orm import Session

    try:
        magic_id_int = int(magi_id)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"MAGI_ID must be an integer: {magi_id!r}") from exc

    with Session(engine) as session:
        return session.get(MAGIC, magic_id_int)


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
    # ``init_sqlite`` is idempotent — creates the file only if absent.
    from magi.bus.db import init_sqlite

    init_sqlite(str(db_path.parent))
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


__all__ = [
    "BootstrapIdentity",
    "bootstrap_magi",
    "bootstrap_first_magi",
    "bootstrap_existing_magi",
    "ensure_private_database",
    "persist_runtime_state",
]