"""RuntimeBook — unified runtime registry for both local and K8s backends.

One row per provisioned MAGI runtime. Replaces the old pair of
``control_runtime_state`` (launcher view: PID / port / workspace) and
``eva_runtimes`` (orchestrator view: K8s Deployment name / namespace /
image) with a single table that discriminates by ``backend_kind``.

- Local mode populates the process fields (``pid``, ``base_url``,
  ``port``, ``workspace_dir``, ``log_dir``, ``audit_log_path``,
  ``spawned_at``, ``stopped_at``); K8s-only fields stay NULL.
- K8s mode populates the resource fields (``deployment_name``,
  ``namespace``, ``image``, ``extra``); the launcher still records
  ``pid`` once the Deployment is up and the pod sidecar reports its
  PID back through the runtime.
- Lifecycle enums (``desired_state`` / ``observed_state``) live once
  here, instead of being duplicated across the two old tables.

FK target: ``runtime_id`` is the same identity used everywhere else
(``magis.adam_id``, the runtime spec's ``magi_id``, etc.) so callers
can pass it without translating between ``magi_id`` and ``runtime_id``.
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
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.library.base import BaseBook
from magi.bus.db.base import Base, utcnow_naive


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
class Runtime:
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
    # K8s-only fields (NULL in local mode).
    deployment_name: str | None = None
    namespace: str | None = None
    image: str | None = None
    extra: str | None = None


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


class _RuntimeRow(Base):
    __tablename__ = "runtime_state"

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
    # K8s-only fields — NULL in local mode.
    deployment_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    namespace: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image: Mapped[str | None] = mapped_column(String(256), nullable=True)
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)


class _ControlPortAllocationRow(Base):
    __tablename__ = "control_port_allocations"

    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_state.runtime_id"),
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


class RuntimeBook(BaseBook[_RuntimeRow, Runtime]):
    model_cls = _RuntimeRow
    dto_cls = Runtime

    def get(self, *, runtime_id: int) -> Runtime | None:
        with self._session() as s:
            row = s.scalar(
                select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[Runtime]:
        with self._session() as s:
            rows = s.scalars(
                select(_RuntimeRow).order_by(_RuntimeRow.runtime_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def upsert(
        self,
        *,
        runtime_id: int,
        backend_kind: str,
        backend_ref: str,
        workspace_dir: str,
        log_dir: str,
        audit_log_path: str,
        port: int | None,
        base_url: str | None,
        deployment_name: str | None = None,
        namespace: str | None = None,
        image: str | None = None,
        extra: str | None = None,
    ) -> Runtime:
        """Insert or update the static config for one runtime.

        Caller is responsible for ``desired_state`` /
        ``observed_state`` lifecycle transitions via the
        :meth:`set_desired_state` / :meth:`set_observed_state` helpers
        below. ``upsert`` only writes the identity and address fields;
        it never pretends a process is alive.
        """
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(
                select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id)
            )
            if row is None:
                row = _RuntimeRow(
                    runtime_id=runtime_id, backend_kind=backend_kind,
                    backend_ref=backend_ref, workspace_dir=workspace_dir,
                    log_dir=log_dir, audit_log_path=audit_log_path,
                    port=port, base_url=base_url,
                    deployment_name=deployment_name, namespace=namespace,
                    image=image, extra=extra, updated_at=now,
                )
                s.add(row)
            else:
                row.backend_kind = backend_kind
                row.backend_ref = backend_ref
                row.workspace_dir = workspace_dir
                row.log_dir = log_dir
                row.audit_log_path = audit_log_path
                row.port = port
                row.base_url = base_url
                row.deployment_name = deployment_name
                row.namespace = namespace
                row.image = image
                row.extra = extra
                row.updated_at = now
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def rename(self, *, runtime_id: int, backend_ref: str) -> Runtime | None:
        """Rename the operator-facing runtime label for one MAGI.

        ``backend_ref`` is deliberately owned by the control-plane record:
        node-local settings are unavailable to the singleton WebUI.  Keeping
        this small mutation in the Book prevents HTTP callers from reaching
        into the MAGIS database to update a row themselves.
        """
        value = backend_ref.strip()
        if not value:
            raise ValueError("runtime name is required")
        with self._session() as s:
            row = s.scalar(
                select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id)
            )
            if row is None:
                return None
            row.backend_ref = value
            row.updated_at = utcnow_naive()
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def set_desired_state(
        self, *, runtime_id: int, desired_state: RuntimeDesiredState
    ) -> Runtime | None:
        """Record a lifecycle intent for a provisioned runtime.

        The launcher/orchestrator remains responsible for observing and
        performing the transition.  This method only persists the requested
        target state; it never pretends that a process has already started or
        stopped.
        """
        with self._session() as s:
            row = s.scalar(
                select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id)
            )
            if row is None:
                return None
            row.desired_state = desired_state
            row.updated_at = utcnow_naive()
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def set_observed_state(
        self,
        *,
        runtime_id: int,
        observed_state: RuntimeObservedState,
        pid: int | None = None,
        spawned_at: datetime | None = None,
        stopped_at: datetime | None = None,
    ) -> Runtime | None:
        """Record a lifecycle observation for a provisioned runtime.

        Companion to :meth:`set_desired_state`: this records *what actually
        happened*.  The launcher/orchestrator calls it after observing the
        process transition (e.g. ``STARTING`` before spawn, ``STARTED`` once
        the runtime is healthy, ``STOPPED`` once it has exited).

        - ``pid`` is recorded only on startup; on stop we clear it.
        - ``spawned_at`` / ``stopped_at`` capture the wall-clock transition
          timestamps; both default to ``None`` to leave prior values intact
          when the caller doesn't care to update them.
        """
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(
                select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id)
            )
            if row is None:
                return None
            row.observed_state = observed_state
            if pid is not None:
                row.pid = pid
            if spawned_at is not None:
                row.spawned_at = spawned_at
            if stopped_at is not None:
                row.stopped_at = stopped_at
                row.pid = None
            row.stale = False
            row.updated_at = now
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def remove(self, *, runtime_id: int) -> bool:
        """Remove a control record after its runtime has been deprovisioned."""
        with self._session() as s:
            row = s.scalar(
                select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id)
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


# -- Control-plane auxiliary Books (port allocations, archive, secrets) --


class PortAllocationBook(BaseBook[_ControlPortAllocationRow, PortAllocation]):
    model_cls = _ControlPortAllocationRow
    dto_cls = PortAllocation

    def get(self, *, runtime_id: int) -> PortAllocation | None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlPortAllocationRow).where(
                    _ControlPortAllocationRow.runtime_id == runtime_id
                )
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[PortAllocation]:
        with self._session() as s:
            rows = s.scalars(select(_ControlPortAllocationRow)).all()
            return [self._row_to_dto(r) for r in rows]

    def allocate(self, *, runtime_id: int, port: int) -> PortAllocation:
        """Record a sticky port allocation for one runtime."""
        now = utcnow_naive()
        with self._session() as s:
            row = _ControlPortAllocationRow(
                port=port, runtime_id=runtime_id, in_use_since=now,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)


class WorkspaceArchiveBook(BaseBook[_ControlWorkspaceArchiveRow, WorkspaceArchive]):
    model_cls = _ControlWorkspaceArchiveRow
    dto_cls = WorkspaceArchive

    def list_all(self) -> list[WorkspaceArchive]:
        with self._session() as s:
            rows = s.scalars(select(_ControlWorkspaceArchiveRow)).all()
            return [self._row_to_dto(r) for r in rows]


class ControlSecretBook(BaseBook[_ControlSecretRow, ControlSecret]):
    model_cls = _ControlSecretRow
    dto_cls = ControlSecret

    def get(self, *, name: str) -> ControlSecret | None:
        with self._session() as s:
            row = s.scalar(
                select(_ControlSecretRow).where(_ControlSecretRow.name == name)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[ControlSecret]:
        with self._session() as s:
            rows = s.scalars(select(_ControlSecretRow)).all()
            return [self._row_to_dto(r) for r in rows]


__all__ = [
    "Runtime",
    "RuntimeBook",
    "RuntimeDesiredState",
    "RuntimeObservedState",
    "PortAllocation",
    "PortAllocationBook",
    "WorkspaceArchive",
    "WorkspaceArchiveBook",
    "ControlSecret",
    "ControlSecretBook",
]