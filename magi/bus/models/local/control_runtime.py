"""Control-plane runtime registry models — stored in the MAGIS database.

These tables track runtime lifecycle state (desired/observed), port
allocations, workspace archives, and the launcher-issued control secret.
They live in the MAGIS database (``MAGI_Societies/<id>-<slug>/magis.db`` locally,
PostgreSQL in K8s) alongside organisation facts like ``magic``, ``magis``,
``magis_memberships``, ``eva_runtimes``, ``control_settings``, and
``control_operators``.

Previously these lived in a separate ``control/local-registry.db`` SQLite
file; they were merged into MAGIS so every deployment profile uses the
same database for all control-plane state.
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.bus.db.base import Base


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
    """One row per Local runtime managed by the launcher."""

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
    """A single sticky port assignment for one runtime."""

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
    """Soft-deleted runtime workspace, kept for restore."""

    __tablename__ = "control_workspace_archive"

    runtime_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archive_path: Mapped[str] = mapped_column(String(500), nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    restored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ControlSecret(Base):
    """The launcher-issued control secret, stored as a salted hash."""

    __tablename__ = "control_secrets"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = [
    "RuntimeDesiredState",
    "RuntimeObservedState",
    "ControlRuntimeState",
    "ControlPortAllocation",
    "ControlWorkspaceArchive",
    "ControlSecret",
]
