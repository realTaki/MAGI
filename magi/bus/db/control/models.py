"""ORM models for the Local control-plane registry.

Phase 3 close-out — the SQLite-backed ``<data_root>/control/local-registry.db``.
K8s Profile continues to read/write its K8s-runtime rows in the central
PostgreSQL MAGIS instead.

Tables:

- ``control_runtime_state``   — per-runtime desired vs observed state,
                              ``base_url``, ``backend_ref``, ``pid``,
                              ``workspace_dir``, log paths, timestamps.
- ``control_port_allocations`` — sticky port assignments in the
                              42101-42999 range (per plan §7.2).
- ``control_workspace_archive`` — soft-deleted runtime workspaces kept
                              for restore (per plan §7.4).
- ``control_secrets``         — the launcher-issued control secret
                              (per plan §11); stored as a salted hash.

Per plan §6.1 / §6.2 business modules never instantiate the ORM
classes; they use :class:`magi.bus.db.control.repository.ControlRepository`
through the :class:`magi.bus.services.control_registry.ControlRegistryService`
facade.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Control-registry SQLAlchemy declarative base.

    Distinct from :class:`magi.bus.db.base.Base` so the OrmRegistry
    alembic migrations don't try to apply K8s-MAGIS revisions here.
    """


class RuntimeDesiredState(str, PyEnum):
    """Operator-requested state of one Runtime."""

    STARTED = "started"
    STOPPED = "stopped"


class RuntimeObservedState(str, PyEnum):
    """Backend-reported state; converges towards ``desired_state``."""

    STARTING = "starting"
    STARTED = "started"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DELETED = "deleted"
    CRASHED = "crashed"
    UNKNOWN = "unknown"


class ControlRuntimeState(Base):
    """One row per runtime the Local supervisor manages."""

    __tablename__ = "control_runtime_state"

    runtime_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backend_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    desired_state: Mapped[RuntimeDesiredState] = mapped_column(
        SAEnum(RuntimeDesiredState), nullable=False, default=RuntimeDesiredState.STOPPED
    )
    observed_state: Mapped[RuntimeObservedState] = mapped_column(
        SAEnum(RuntimeObservedState), nullable=False, default=RuntimeObservedState.UNKNOWN
    )
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    backend_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    workspace_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    log_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    audit_log_path: Mapped[str] = mapped_column(String(500), nullable=False)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spawned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    port_alloc: Mapped["ControlPortAllocation | None"] = relationship(
        back_populates="runtime_state",
        uselist=False,
    )


class ControlPortAllocation(Base):
    """A single sticky port assignment for one runtime.

    Per plan §7.2 the Local Profile assigns ports from the fixed
    range ``42101-42999`` and persists the assignment here; port is
    released only when the runtime is deleted (per plan §7.4).
    """

    __tablename__ = "control_port_allocations"

    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("control_runtime_state.runtime_id"), unique=True, nullable=False
    )
    in_use_since: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    runtime_state: Mapped[ControlRuntimeState] = relationship(back_populates="port_alloc")

    __table_args__ = (
        UniqueConstraint("runtime_id", name="uq_control_port_alloc_runtime"),
    )


class ControlWorkspaceArchive(Base):
    """Soft-deleted runtime workspace, kept for restore.

    Per plan §7.4 ``delete`` moves the workspace to an ``archive/``
    sibling directory and records the path here; ``stop`` keeps the
    row in ``control_runtime_state`` and never touches this table.
    """

    __tablename__ = "control_workspace_archive"

    runtime_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archive_path: Mapped[str] = mapped_column(String(500), nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    restored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ControlSecret(Base):
    """The launcher-issued control secret (per plan §11).

    Stored as a salted hash; raw secret never lands in the registry.
    API clients authenticate via ``X-MAGI-Control-Secret`` header on
    the loopback-only control-plane API.
    """

    __tablename__ = "control_secrets"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = [
    "Base",
    "RuntimeDesiredState",
    "RuntimeObservedState",
    "ControlRuntimeState",
    "ControlPortAllocation",
    "ControlWorkspaceArchive",
    "ControlSecret",
]
