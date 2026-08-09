"""Explicit storage provisioning for MAGI.

Opening a BUS is read-only with respect to deployment shape.  This module is
the sole production owner of first-time directory creation, schema material
isation and node defaults.  It is called by provisioning commands only; a
runtime must use :func:`magi.bus.open_bus` instead.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from magi.bus.db.engine import EngineFactory
from magi.bus.db.schema import apply_initial_schema

SCHEMA_REVISION = "0001"


class StorageNotProvisioned(RuntimeError):
    """Raised when a runtime attempts to open storage before provisioning."""


def _revision_table(factory: EngineFactory) -> None:
    with factory.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS magi_schema_revisions "
                "(scope VARCHAR(16) PRIMARY KEY, revision VARCHAR(32) NOT NULL)"
            )
        )


def _mark_provisioned(factory: EngineFactory, *, scope: str) -> None:
    _revision_table(factory)
    with factory.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM magi_schema_revisions WHERE scope = :scope"),
            {"scope": scope},
        )
        connection.execute(
            text(
                "INSERT INTO magi_schema_revisions (scope, revision) "
                "VALUES (:scope, :revision)"
            ),
            {"scope": scope, "revision": SCHEMA_REVISION},
        )


def require_provisioned(factory: EngineFactory, *, scope: str) -> None:
    """Require the exact schema revision before a process opens a store."""
    try:
        with factory.engine.connect() as connection:
            revision = connection.execute(
                text(
                    "SELECT revision FROM magi_schema_revisions "
                    "WHERE scope = :scope"
                ),
                {"scope": scope},
            ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise StorageNotProvisioned(
            f"{scope} storage is not provisioned; run the explicit provisioning command"
        ) from exc
    if revision != SCHEMA_REVISION:
        raise StorageNotProvisioned(
            f"{scope} storage revision is {revision!r}; expected {SCHEMA_REVISION!r}. "
            "Run the explicit provisioning command."
        )


def provision_node_storage(
    *,
    state_dir: str,
    magis_url: str | None,
    prompts_dir: str | None = None,
):
    """Provision one fresh node store and its MAGIS store when supplied.

    ``state_dir`` must be the canonical ``<workspace>/memories`` directory.
    Provisioning is idempotent for the current revision, but it never accepts
    the retired ``<workspace>/magi.db`` path: callers must start from a clean
    state as part of this architecture cutover.
    """
    state_path = Path(state_dir).resolve()
    if state_path.name != "memories":
        raise ValueError("node state_dir must end in 'memories'")
    workspace_dir = state_path.parent
    retired_db = workspace_dir / "magi.db"
    if retired_db.exists():
        raise StorageNotProvisioned(
            f"retired node database exists at {retired_db}; clean the workspace before provisioning"
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    for name in ("memories", "skills", "logs", "run"):
        (workspace_dir / name).mkdir(parents=True, exist_ok=True)

    # Importing and wiring Books registers all BUS-owned metadata before the
    # one explicit schema materialisation below.
    from magi.bus.bootstrap import _open_with_dirs, _ensure_workspace_soul, _resolve_prompts_dir

    bus = _open_with_dirs(
        state_dir=str(state_path),
        magis_url=magis_url,
        prompts_dir=prompts_dir,
        allow_unprovisioned=True,
    )
    apply_initial_schema(bus._local_factory)
    _mark_provisioned(bus._local_factory, scope="node")
    if bus._magis_factory is not None:
        apply_initial_schema(bus._magis_factory)
        _mark_provisioned(bus._magis_factory, scope="magis")

    bus.messages_book.ensure_fts()
    if not bus.settings_book.get(key="auth.signing_key"):
        bus.settings_book.set(key="auth.signing_key", value=secrets.token_hex(32))
    resolved_prompts = _resolve_prompts_dir(prompts_dir)
    if resolved_prompts is not None:
        _ensure_workspace_soul(workspace_dir, resolved_prompts)
    return bus


__all__ = [
    "SCHEMA_REVISION",
    "StorageNotProvisioned",
    "provision_node_storage",
    "require_provisioned",
]
