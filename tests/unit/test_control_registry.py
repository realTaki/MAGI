"""Control-plane runtime registry tests — backed by MAGIS local engine.

The control tables (control_runtime_state, control_port_allocations, …)
now live in the same MAGIS SQLite as organisation facts — no separate
``control/local-registry.db``.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

import pytest

from magi.bus.db.control.repository import (
    ControlRepository,
    PortAllocationDTO,
    PortAlreadyAllocated,
    RuntimeStateDTO,
    UnknownRuntime,
)
from magi.bus.db.base import Base
from magi.bus.db.magis.local_engine import build as build_local_engine
from magi.bus.models.local.control_runtime import (
    ControlPortAllocation,
    ControlRuntimeState,
    ControlSecret,
    ControlWorkspaceArchive,
    RuntimeDesiredState,
    RuntimeObservedState,
)


@pytest.fixture()
def repo(tmp_path: Path) -> ControlRepository:
    engine = build_local_engine(tmp_path / "magis-test")
    # Ensure control-runtime tables exist in the MAGIS database.
    Base.metadata.create_all(
        engine,
        tables=[
            ControlRuntimeState.__table__,
            ControlPortAllocation.__table__,
            ControlWorkspaceArchive.__table__,
            ControlSecret.__table__,
        ],
    )
    return ControlRepository(engine)


def test_upsert_then_record_spawn_then_get(repo: ControlRepository) -> None:
    repo.upsert_desired_state(
        1, "local", RuntimeDesiredState.STARTED
    )
    repo.attach_paths(
        1,
        workspace_dir=Path("/tmp/adam-ws"),
        log_dir=Path("/tmp/adam-logs"),
        audit_log_path=Path("/tmp/adam-audit.log"),
        backend_ref="cli-adam",
    )
    repo.record_spawn(1, pid=12345, base_url="http://127.0.0.1:42101", port=42101)
    snap = repo.get_runtime(1)
    assert snap.runtime_id == 1
    assert snap.observed_state == RuntimeObservedState.STARTED
    assert snap.pid == 12345
    assert snap.port == 42101
    assert snap.desired_state == RuntimeDesiredState.STARTED


def test_port_allocator_sticky_across_stop(repo: ControlRepository) -> None:
    alloc1 = repo.allocate_port(7)
    assert 42101 <= alloc1.port <= 42999
    alloc2 = repo.allocate_port(7)
    assert alloc1.port == alloc2.port
    repo.release_port(7)
    alloc3 = repo.allocate_port(8)
    assert alloc3.port >= 42101


def test_port_allocator_unique_per_runtime(repo: ControlRepository) -> None:
    seen: set[int] = set()
    for rid in range(1, 11):
        dto = repo.allocate_port(rid)
        assert dto.port not in seen
        seen.add(dto.port)


def test_record_stop_clears_pid(repo: ControlRepository) -> None:
    repo.upsert_desired_state(9, "local", RuntimeDesiredState.STARTED)
    repo.record_spawn(9, pid=99, base_url="http://127.0.0.1:42150", port=42150)
    repo.record_stop(9)
    snap = repo.get_runtime(9)
    assert snap.observed_state == RuntimeObservedState.STOPPED
    assert snap.pid is None
    assert snap.base_url is None
    assert snap.stopped_at is not None


def test_unknown_runtime_raises() -> None:
    engine = build_local_engine(Path("/tmp") / uuid.uuid4().hex)
    Base.metadata.create_all(
        engine,
        tables=[ControlRuntimeState.__table__],
    )
    repo = ControlRepository(engine)
    with pytest.raises(UnknownRuntime):
        repo.get_runtime(99999)


def test_secrets_round_trip(repo: ControlRepository) -> None:
    repo.put_secret("control-plane", "s3cret-token-AAA")
    assert repo.verify_secret("control-plane", "s3cret-token-AAA")
    assert not repo.verify_secret("control-plane", "wrong-token")
    assert not repo.verify_secret("other-name", "s3cret-token-AAA")


def test_concurrent_port_allocations(tmp_path: Path) -> None:
    engine = build_local_engine(tmp_path / "magis-concurrent")
    Base.metadata.create_all(
        engine,
        tables=[
            ControlRuntimeState.__table__,
            ControlPortAllocation.__table__,
        ],
    )
    repo = ControlRepository(engine)

    errors: list[Exception] = []
    results: dict[int, int] = {}
    lock = threading.Lock()

    def worker(rid: int) -> None:
        try:
            dto = repo.allocate_port(rid)
            with lock:
                results[rid] = dto.port
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(rid,))
        for rid in range(1, 21)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"thread errors: {errors}"
    assert len(results) == 20
    ports = list(results.values())
    assert len(set(ports)) == 20, f"duplicate ports detected: {ports}"


def test_archive_then_forget(repo: ControlRepository) -> None:
    repo.upsert_desired_state(11, "local", RuntimeDesiredState.STARTED)
    repo.record_spawn(11, pid=11111, base_url="http://127.0.0.1:42160", port=42160)
    repo.archive_workspace(11, Path("/tmp/archive/adam-old"))
    repo.forget(11)
    with pytest.raises(UnknownRuntime):
        repo.get_runtime(11)
