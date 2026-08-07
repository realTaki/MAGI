"""ControlBook — control-plane runtime registry (``control_*`` tables).

Four tables in the MAGIS database:
- ``control_runtime_state``     — desired/observed state per runtime
- ``control_port_allocations`` — sticky port assignments
- ``control_workspace_archive`` — soft-deleted workspace records
- ``control_secrets``           — launcher-issued control secret hashes

Schema mirrors the old bus's tables.  new_bus's ControlBook
preserves the original `RuntimeDesiredState` / `RuntimeObservedState`
enums (string-backed).
"""

from __future__ import annotations

from dataclasses import dataclass
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
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


class RuntimeDesiredState(str, PyEnum):
    STARTED = "started"
    STOPPED = "stopped"


class RuntimeObservedState(str, PyEnum):
    STARTING = "starting"
    STARTED = "started"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DELETED = "deleted"
    CRASHED = "crashed"
    UNKNOWN = "unknown"


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ControlRuntime:
    runtime_id: int
    backend_kind: str
    desired_state: str
    observed_state: str
    backend_ref: str
    workspace_dir: str
    log_dir: str
    audit_log_path: str
    pid: int | None = None
    base_url: str | None = None
    port: int | None = None
    spawned_at: datetime | None = None
    stopped_at: datetime | None = None
    updated_at: datetime | None = None
    stale: bool = False


@dataclass(frozen=True, slots=True)
class PortAllocation:
    port: int
    runtime_id: int
    in_use_since: datetime
    released_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceArchive:
    runtime_id: int
    archive_path: str
    archived_at: datetime
    restored: bool = False


@dataclass(frozen=True, slots=True)
class ControlSecret:
    name: str
    secret_hash: bytes
    salt: bytes
    created_at: datetime


# -- internal ORM --------------------------------------------------------


class _ControlRuntimeRow(Base):
    __tablename__ = "control_runtime_state"

    runtime_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backend_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    desired_state: Mapped[RuntimeDesiredState] = mapped_column(
        SAEnum(RuntimeDesiredState), nullable=False,
        default=RuntimeDesiredState.STOPPED,
    )
    observed_state: Mapped[RuntimeObservedState] = mapped_column(
        SAEnum(RuntimeObservedState), nullable=False,
        default=RuntimeObservedState.UNKNOWN,
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


class _ControlPortAllocationRow(Base):
    __tablename__ = "control_port_allocations"

    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("control_runtime_state.runtime_id"),
        unique=True, nullable=False,
    )
    in_use_since: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("runtime_id", name="uq_control_port_alloc_runtime"),
    )


class _ControlWorkspaceArchiveRow(Base):
    __tablename__ = "control_workspace_archive"

    runtime_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archive_path: Mapped[str] = mapped_column(String(500), nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    restored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class _ControlSecretRow(Base):
    __tablename__ = "control_secrets"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# -- Books ---------------------------------------------------------------


class ControlRuntimeBook(BaseBook[_ControlRuntimeRow, ControlRuntime]):
    model_cls = _ControlRuntimeRow
    dto_cls = ControlRuntime

    def get(self, *, runtime_id: int) -> ControlRuntime | None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlRuntimeRow)
                .where(_ControlRuntimeRow.runtime_id == runtime_id)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[ControlRuntime]:
        with self._session() as s:
            rows = s.scalars(
                select(_ControlRuntimeRow).order_by(_ControlRuntimeRow.runtime_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def upsert_desired(self, *, runtime_id: int, backend_kind: str,
                       desired: str) -> None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlRuntimeRow)
                .where(_ControlRuntimeRow.runtime_id == runtime_id)
            )
            if row is None:
                row = _ControlRuntimeRow(
                    runtime_id=runtime_id, backend_kind=backend_kind,
                    desired_state=RuntimeDesiredState(desired),
                    observed_state=RuntimeObservedState.UNKNOWN,
                    backend_ref="", workspace_dir="", log_dir="",
                    audit_log_path="",
                    updated_at=utcnow_naive(),
                )
                s.add(row)
            else:
                row.desired_state = RuntimeDesiredState(desired)
                row.updated_at = utcnow_naive()
            s.commit()

    def record_spawn(self, *, runtime_id: int, pid: int,
                      base_url: str, port: int) -> None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlRuntimeRow)
                .where(_ControlRuntimeRow.runtime_id == runtime_id)
            )
            if row is None:
                return
            now = utcnow_naive()
            row.observed_state = RuntimeObservedState.STARTED
            row.pid = pid
            row.base_url = base_url
            row.port = port
            row.spawned_at = now
            row.stale = False
            row.updated_at = now
            s.commit()

    def record_stop(self, *, runtime_id: int) -> None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlRuntimeRow)
                .where(_ControlRuntimeRow.runtime_id == runtime_id)
            )
            if row is None:
                return
            now = utcnow_naive()
            row.observed_state = RuntimeObservedState.STOPPED
            row.pid = None
            row.base_url = None
            row.stopped_at = now
            row.updated_at = now
            s.commit()

    def mark_stale(self, *, runtime_id: int, stale: bool = True) -> None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlRuntimeRow)
                .where(_ControlRuntimeRow.runtime_id == runtime_id)
            )
            if row is None:
                return
            row.stale = stale
            row.updated_at = utcnow_naive()
            s.commit()


class PortAllocationBook(BaseBook[_ControlPortAllocationRow, PortAllocation]):
    model_cls = _ControlPortAllocationRow
    dto_cls = PortAllocation

    def get(self, *, runtime_id: int) -> PortAllocation | None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlPortAllocationRow)
                .where(_ControlPortAllocationRow.runtime_id == runtime_id)
            )
            return self._row_to_dto(row) if row else None

    def list_held_ports(self) -> list[int]:
        with self._session() as s:
            rows = s.scalars(
                select(_ControlPortAllocationRow.port)
                .where(_ControlPortAllocationRow.released_at.is_(None))
            ).all()
            return list(rows)

    def allocate(self, *, runtime_id: int, port: int) -> PortAllocation:
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(
                select(_ControlPortAllocationRow)
                .where(_ControlPortAllocationRow.runtime_id == runtime_id)
            )
            if row is None:
                row = _ControlPortAllocationRow(
                    port=port, runtime_id=runtime_id, in_use_since=now,
                )
                s.add(row)
            else:
                row.port = port
                row.runtime_id = runtime_id
                row.in_use_since = now
                row.released_at = None
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def release(self, *, runtime_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(
                select(_ControlPortAllocationRow)
                .where(
                    _ControlPortAllocationRow.runtime_id == runtime_id,
                    _ControlPortAllocationRow.released_at.is_(None),
                )
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


class WorkspaceArchiveBook(BaseBook[_ControlWorkspaceArchiveRow, WorkspaceArchive]):
    model_cls = _ControlWorkspaceArchiveRow
    dto_cls = WorkspaceArchive

    def get(self, *, runtime_id: int) -> WorkspaceArchive | None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlWorkspaceArchiveRow)
                .where(_ControlWorkspaceArchiveRow.runtime_id == runtime_id)
            )
            return self._row_to_dto(row) if row else None

    def archive(self, *, runtime_id: int, archive_path: str) -> WorkspaceArchive:
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(
                select(_ControlWorkspaceArchiveRow)
                .where(_ControlWorkspaceArchiveRow.runtime_id == runtime_id)
            )
            if row is None:
                row = _ControlWorkspaceArchiveRow(
                    runtime_id=runtime_id, archive_path=archive_path,
                    archived_at=now, restored=False,
                )
                s.add(row)
            else:
                row.archive_path = archive_path
                row.archived_at = now
                row.restored = False
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)


class ControlSecretBook(BaseBook[_ControlSecretRow, ControlSecret]):
    model_cls = _ControlSecretRow
    dto_cls = ControlSecret

    def get(self, *, name: str) -> ControlSecret | None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlSecretRow).where(_ControlSecretRow.name == name)
            )
            return self._row_to_dto(row) if row else None

    def put(self, *, name: str, secret_hash: bytes, salt: bytes) -> None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlSecretRow).where(_ControlSecretRow.name == name)
            )
            now = utcnow_naive()
            if row is None:
                row = _ControlSecretRow(
                    name=name, secret_hash=secret_hash,
                    salt=salt, created_at=now,
                )
                s.add(row)
            else:
                row.secret_hash = secret_hash
                row.salt = salt
                row.created_at = now
            s.commit()

    def verify(self, *, name: str, raw: bytes) -> bool:
        import hashlib, hmac

        with self._session() as s:
            row = s.scalar(
                select(_ControlSecretRow).where(_ControlSecretRow.name == name)
            )
            if row is None:
                return False
            pepper = b"magi-launcher-v1"
            expected = hashlib.sha256(pepper + row.salt + raw).digest()
            return hmac.compare_digest(expected, row.secret_hash)


__all__ = [
    "ControlRuntime",
    "PortAllocation",
    "WorkspaceArchive",
    "ControlSecret",
    "ControlRuntimeBook",
    "PortAllocationBook",
    "WorkspaceArchiveBook",
    "ControlSecretBook",
    "RuntimeDesiredState",
    "RuntimeObservedState",
    "_ControlRuntimeRow",
    "_ControlPortAllocationRow",
    "_ControlWorkspaceArchiveRow",
    "_ControlSecretRow",
]
