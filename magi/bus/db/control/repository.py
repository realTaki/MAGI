"""Bus-facing command/query surface over the runtime registry.

The repository operates on the MAGIS database engine — the same engine
that holds organisation facts (``magic``, ``magis``, ``magis_memberships``,
``eva_runtimes``).  Previously this was a separate ``control/local-registry.db``
SQLite; it was merged into MAGIS so every deployment profile uses one
database for all control-plane state.

All business modules talk to this repository through
:class:`magi.bus.services.control_registry.ControlRegistryService`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from magi.bus.db.models.local.control_runtime import (
    ControlPortAllocation,
    ControlRuntimeState,
    ControlSecret,
    ControlWorkspaceArchive,
    RuntimeDesiredState,
    RuntimeObservedState,
)


# -- DTOs --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuntimeStateDTO:
    """Bus-facing snapshot of one Runtime's desired vs observed state."""

    runtime_id: int
    backend_kind: str
    desired_state: RuntimeDesiredState
    observed_state: RuntimeObservedState
    pid: Optional[int]
    base_url: Optional[str]
    backend_ref: str
    workspace_dir: Path
    log_dir: Path
    audit_log_path: Path
    port: Optional[int]
    spawned_at: Optional[datetime]
    stopped_at: Optional[datetime]
    updated_at: datetime
    stale: bool


@dataclass(frozen=True, slots=True)
class PortAllocationDTO:
    port: int
    runtime_id: int
    in_use_since: datetime
    released_at: Optional[datetime]


# -- Errors ------------------------------------------------------------------


class UnknownRuntime(KeyError):
    """No row in ``control_runtime_state`` for the requested ``runtime_id``."""

    def __init__(self, runtime_id: int) -> None:
        super().__init__(f"unknown runtime_id={runtime_id}")
        self.runtime_id = runtime_id


class PortAlreadyAllocated(RuntimeError):
    """A second runtime tried to claim a port already held."""

    def __init__(self, port: int) -> None:
        super().__init__(f"port {port} already allocated")
        self.port = port


# -- Repository --------------------------------------------------------------


class ControlRepository:
    """Single facade over the runtime-registry tables in the MAGIS database.

    Thread-safe — the SQLAlchemy :class:`Engine` is process-shared and
    serialises writes via ``BEGIN IMMEDIATE``.
    """

    PORT_RANGE_START = 42101
    PORT_RANGE_END = 42999

    _PEPPER = b"magi-launcher-v1"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._Session: sessionmaker[Session] = sessionmaker(
            bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
        )

    # -- runtime lifecycle ------------------------------------------------

    def upsert_desired_state(self, runtime_id: int, backend_kind: str, desired: RuntimeDesiredState) -> None:
        with self._Session() as session:
            row = session.get(ControlRuntimeState, runtime_id)
            if row is None:
                row = ControlRuntimeState(
                    runtime_id=runtime_id,
                    backend_kind=backend_kind,
                    desired_state=desired,
                    observed_state=RuntimeObservedState.UNKNOWN,
                    backend_ref="",
                    workspace_dir="",
                    log_dir="",
                    audit_log_path="",
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(row)
            else:
                row.desired_state = desired
                row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def attach_paths(
        self,
        runtime_id: int,
        workspace_dir: Path,
        log_dir: Path,
        audit_log_path: Path,
        backend_ref: str,
    ) -> None:
        with self._Session() as session:
            row = session.get(ControlRuntimeState, runtime_id)
            if row is None:
                raise UnknownRuntime(runtime_id)
            row.workspace_dir = str(workspace_dir)
            row.log_dir = str(log_dir)
            row.audit_log_path = str(audit_log_path)
            row.backend_ref = backend_ref
            row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def record_spawn(self, runtime_id: int, pid: int, base_url: str, port: int) -> None:
        now = datetime.now(timezone.utc)
        with self._Session() as session:
            row = session.get(ControlRuntimeState, runtime_id)
            if row is None:
                raise UnknownRuntime(runtime_id)
            row.observed_state = RuntimeObservedState.STARTED
            row.pid = pid
            row.base_url = base_url
            row.port = port
            row.spawned_at = now
            row.stale = False
            row.updated_at = now
            session.commit()

    def record_observed(self, runtime_id: int, observed: RuntimeObservedState) -> None:
        with self._Session() as session:
            row = session.get(ControlRuntimeState, runtime_id)
            if row is None:
                raise UnknownRuntime(runtime_id)
            row.observed_state = observed
            row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def record_stop(self, runtime_id: int) -> None:
        now = datetime.now(timezone.utc)
        with self._Session() as session:
            row = session.get(ControlRuntimeState, runtime_id)
            if row is None:
                raise UnknownRuntime(runtime_id)
            row.observed_state = RuntimeObservedState.STOPPED
            row.pid = None
            row.base_url = None
            row.stopped_at = now
            row.updated_at = now
            session.commit()

    def archive_workspace(self, runtime_id: int, archive_path: Path) -> None:
        now = datetime.now(timezone.utc)
        with self._Session() as session:
            archive = session.get(ControlWorkspaceArchive, runtime_id)
            if archive is None:
                archive = ControlWorkspaceArchive(
                    runtime_id=runtime_id,
                    archive_path=str(archive_path),
                    archived_at=now,
                )
                session.add(archive)
            else:
                archive.archive_path = str(archive_path)
                archive.archived_at = now
                archive.restored = False
            session.commit()

    def forget(self, runtime_id: int) -> None:
        with self._Session() as session:
            row = session.get(ControlRuntimeState, runtime_id)
            if row is not None:
                session.delete(row)
            alloc = session.get(ControlPortAllocation, runtime_id)
            if alloc is not None:
                session.delete(alloc)
            session.commit()

    def list_runtimes(self) -> list[RuntimeStateDTO]:
        with self._Session() as session:
            rows = session.query(ControlRuntimeState).order_by(ControlRuntimeState.runtime_id).all()
            return [_to_dto(r, r.port_alloc.port if r.port_alloc else None) for r in rows]

    def get_runtime(self, runtime_id: int) -> RuntimeStateDTO:
        with self._Session() as session:
            row = session.get(ControlRuntimeState, runtime_id)
            if row is None:
                raise UnknownRuntime(runtime_id)
            return _to_dto(row, row.port_alloc.port if row.port_alloc else None)

    def list_stale(self) -> list[RuntimeStateDTO]:
        with self._Session() as session:
            rows = (
                session.query(ControlRuntimeState)
                .filter(ControlRuntimeState.stale.is_(True))
                .order_by(ControlRuntimeState.runtime_id)
                .all()
            )
            return [_to_dto(r, r.port_alloc.port if r.port_alloc else None) for r in rows]

    def mark_stale(self, runtime_id: int, stale: bool = True) -> None:
        with self._Session() as session:
            row = session.get(ControlRuntimeState, runtime_id)
            if row is None:
                raise UnknownRuntime(runtime_id)
            row.stale = stale
            row.updated_at = datetime.now(timezone.utc)
            session.commit()

    # -- port allocator ----------------------------------------------------

    def allocate_port(self, runtime_id: int) -> PortAllocationDTO:
        with self._Session() as session:
            existing = (
                session.query(ControlPortAllocation)
                .filter(ControlPortAllocation.runtime_id == runtime_id)
                .one_or_none()
            )
            if existing is not None and existing.released_at is None:
                return PortAllocationDTO(
                    port=existing.port,
                    runtime_id=existing.runtime_id,
                    in_use_since=existing.in_use_since,
                    released_at=existing.released_at,
                )
            parent = session.get(ControlRuntimeState, runtime_id)
            if parent is None:
                parent = ControlRuntimeState(
                    runtime_id=runtime_id,
                    backend_kind="cli",
                    desired_state=RuntimeDesiredState.STARTED,
                    observed_state=RuntimeObservedState.UNKNOWN,
                    backend_ref="",
                    workspace_dir="",
                    log_dir="",
                    audit_log_path="",
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(parent)
                session.flush()
            held = {
                p for (p,) in session.query(ControlPortAllocation.port)
                .filter(ControlPortAllocation.released_at.is_(None))
                .all()
            }
            for port in range(self.PORT_RANGE_START, self.PORT_RANGE_END + 1):
                if port in held:
                    continue
                now = datetime.now(timezone.utc)
                if existing is None:
                    session.add(
                        ControlPortAllocation(
                            port=port, runtime_id=runtime_id, in_use_since=now
                        )
                    )
                else:
                    existing.port = port
                    existing.runtime_id = runtime_id
                    existing.in_use_since = now
                    existing.released_at = None
                session.commit()
                return PortAllocationDTO(
                    port=port, runtime_id=runtime_id, in_use_since=now, released_at=None
                )
            raise RuntimeError(
                f"no free ports in {self.PORT_RANGE_START}-{self.PORT_RANGE_END}"
            )

    def release_port(self, runtime_id: int) -> None:
        with self._Session() as session:
            alloc = (
                session.query(ControlPortAllocation)
                .filter(
                    ControlPortAllocation.runtime_id == runtime_id,
                    ControlPortAllocation.released_at.is_(None),
                )
                .one_or_none()
            )
            if alloc is None:
                return
            session.delete(alloc)
            session.commit()

    # -- secrets -----------------------------------------------------------

    def put_secret(self, name: str, raw: str) -> None:
        salt = secrets.token_bytes(32)
        h = hashlib.sha256(self._PEPPER + salt + raw.encode("utf-8")).digest()
        now = datetime.now(timezone.utc)
        with self._Session() as session:
            row = session.get(ControlSecret, name)
            if row is None:
                session.add(
                    ControlSecret(name=name, secret_hash=h, salt=salt, created_at=now)
                )
            else:
                row.secret_hash = h
                row.salt = salt
                row.created_at = now
            session.commit()

    def verify_secret(self, name: str, raw: str) -> bool:
        with self._Session() as session:
            row = session.get(ControlSecret, name)
            if row is None:
                return False
            expected = hashlib.sha256(self._PEPPER + row.salt + raw.encode("utf-8")).digest()
            return hmac.compare_digest(expected, row.secret_hash)


def _to_dto(row: ControlRuntimeState, port: int | None) -> RuntimeStateDTO:
    return RuntimeStateDTO(
        runtime_id=row.runtime_id,
        backend_kind=row.backend_kind,
        desired_state=row.desired_state,
        observed_state=row.observed_state,
        pid=row.pid,
        base_url=row.base_url,
        backend_ref=row.backend_ref,
        workspace_dir=Path(row.workspace_dir),
        log_dir=Path(row.log_dir),
        audit_log_path=Path(row.audit_log_path),
        port=row.port if row.port else port,
        spawned_at=row.spawned_at,
        stopped_at=row.stopped_at,
        updated_at=row.updated_at,
        stale=row.stale,
    )


__all__ = [
    "ControlRepository",
    "RuntimeStateDTO",
    "PortAllocationDTO",
    "UnknownRuntime",
    "PortAlreadyAllocated",
]
