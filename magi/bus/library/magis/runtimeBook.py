"""RuntimeBook — single unified runtime registry for MAGI Society.

One row per provisioned MAGI runtime. Replaces the previous quartet of
tables — ``control_runtime_state`` (launcher view), ``eva_runtimes``
(orchestrator view), ``control_port_allocations`` (sticky port
allocations), ``control_workspace_archive`` (soft-deleted tombstones)
— with a single ``runtime_state`` table that discriminates by
``backend_kind``.

Field groups on the unified row
-------------------------------

- **Identity + lifecycle** — ``runtime_id`` (PK, = MAGI 身份),
  ``backend_kind``, ``desired_state``, ``observed_state``,
  ``updated_at``, ``stale``.
- **Local-mode address** — ``backend_ref``, ``workspace_dir``,
  ``log_dir``, ``audit_log_path``, ``pid``, ``port``, ``base_url``,
  ``spawned_at``, ``stopped_at``.
- **K8s-mode address** — ``deployment_name``, ``namespace``,
  ``image``, ``extra``.
- **Sticky port allocation** — ``port_in_use_since``,
  ``port_released_at``.  ``port_in_use_since`` is set when the
  launcher first claims a port for this runtime;
  ``port_released_at`` is set when the orchestrator hands it back.
  Rows where ``port_released_at IS NULL`` are the "active
  allocations" enumerated by ``list_allocated_ports``.
- **Workspace archive (tombstone)** — ``archive_path``,
  ``archived_at``, ``restored``.  Set only when a runtime's
  workspace has been soft-deleted; ``restored=True`` records that
  it was later brought back.

The unified design means:

- `_validate_runtime_identity` only needs one Book lookup (no JOIN).
- `_register_local_runtime` records the port allocation in the same
  upsert as the static config — no second Book write.
- `available_magi` filters in-process; no cross-table read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    LargeBinary,
    String,
    Text,
    select,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.library.base import BaseBook


class RuntimeDesiredState(StrEnum):
    STARTED = "started"
    STOPPED = "stopped"


class RuntimeObservedState(StrEnum):
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
    # Sticky port allocation (NULL for an un-allocated runtime).
    port_in_use_since: datetime | None = None
    port_released_at: datetime | None = None
    # Workspace tombstone (NULL for live runtimes).
    archive_path: str | None = None
    archived_at: datetime | None = None
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
        SAEnum(RuntimeDesiredState),
        nullable=False,
        default=RuntimeDesiredState.STOPPED,
    )
    observed_state: Mapped[RuntimeObservedState] = mapped_column(
        SAEnum(RuntimeObservedState),
        nullable=False,
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
    # Sticky port allocation.
    port_in_use_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    port_released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Workspace tombstone.
    archive_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[Runtime]:
        with self._session() as s:
            rows = s.scalars(select(_RuntimeRow).order_by(_RuntimeRow.runtime_id)).all()
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
        allocate_port: bool = True,
    ) -> Runtime:
        """Insert or update the static config for one runtime.

        Caller is responsible for ``desired_state`` /
        ``observed_state`` lifecycle transitions via the
        :meth:`set_desired_state` / :meth:`set_observed_state` helpers
        below. ``upsert`` only writes the identity and address fields;
        it never pretends a process is alive.

        When ``allocate_port=True`` (default) and ``port`` is set, the
        sticky port allocation is also recorded in the same write
        (``port_in_use_since = now``, ``port_released_at = NULL``).
        Pass ``allocate_port=False`` to skip — useful for ops callers
        that only want to refresh metadata.
        """
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
            if row is None:
                row = _RuntimeRow(
                    runtime_id=runtime_id,
                    backend_kind=backend_kind,
                    backend_ref=backend_ref,
                    workspace_dir=workspace_dir,
                    log_dir=log_dir,
                    audit_log_path=audit_log_path,
                    port=port,
                    base_url=base_url,
                    deployment_name=deployment_name,
                    namespace=namespace,
                    image=image,
                    extra=extra,
                    updated_at=now,
                    port_in_use_since=now if (allocate_port and port is not None) else None,
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
                if allocate_port and port is not None and row.port_in_use_since is None:
                    row.port_in_use_since = now
                    row.port_released_at = None
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
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
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
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
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
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
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

    def allocate_port(self, *, runtime_id: int, port: int) -> Runtime | None:
        """Record (or refresh) a sticky port allocation.

        Caller is expected to have already inserted the runtime row
        via :meth:`upsert`; this method only stamps the
        ``port_in_use_since`` field if missing, clears
        ``port_released_at``, and updates the live ``port``.  Returns
        ``None`` if no such runtime exists.
        """
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
            if row is None:
                return None
            if row.port_in_use_since is None:
                row.port_in_use_since = now
            row.port_released_at = None
            row.port = port
            row.updated_at = now
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def release_port(self, *, runtime_id: int) -> Runtime | None:
        """Hand back the sticky port allocation for one runtime.

        The next :meth:`allocate_port` (or :meth:`upsert`) call will
        record a new ``port_in_use_since``.  The ``port`` field is
        cleared so the row no longer advertises a live address.
        """
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
            if row is None:
                return None
            if row.port_in_use_since is None:
                return self._row_to_dto(row)
            row.port_released_at = now
            row.port = None
            row.updated_at = now
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def list_allocated_ports(self) -> set[int]:
        """Return the set of currently-allocated ports (no released rows).

        Used by ``create_node`` to pick the next free sticky port for
        a new runtime.  Rows with ``port_released_at IS NOT NULL`` are
        skipped — they have handed their port back.
        """
        with self._session() as s:
            rows = s.scalars(
                select(_RuntimeRow).where(
                    _RuntimeRow.port_released_at.is_(None),
                    _RuntimeRow.port.is_not(None),
                )
            ).all()
            return {row.port for row in rows if row.port is not None}

    def archive_workspace(self, *, runtime_id: int, archive_path: str) -> Runtime | None:
        """Record a workspace tombstone for a soft-deleted runtime."""
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
            if row is None:
                return None
            row.archive_path = archive_path
            row.archived_at = now
            row.restored = False
            row.updated_at = now
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def restore_workspace(self, *, runtime_id: int) -> Runtime | None:
        """Mark an archived workspace as restored."""
        now = utcnow_naive()
        with self._session() as s:
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
            if row is None:
                return None
            row.restored = True
            row.updated_at = now
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def remove(self, *, runtime_id: int) -> bool:
        """Remove a control record after its runtime has been deprovisioned."""
        with self._session() as s:
            row = s.scalar(select(_RuntimeRow).where(_RuntimeRow.runtime_id == runtime_id))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


# -- Control-plane secret Book (kept separate — orthogonal concern) -----


class ControlSecretBook(BaseBook[_ControlSecretRow, ControlSecret]):
    model_cls = _ControlSecretRow
    dto_cls = ControlSecret

    def get(self, *, name: str) -> ControlSecret | None:
        with self._session() as s:
            row = s.scalar(select(_ControlSecretRow).where(_ControlSecretRow.name == name))
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
    "ControlSecret",
    "ControlSecretBook",
]
