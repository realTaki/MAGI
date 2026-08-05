"""Phase 9 — Local control-plane registry validation tests.

Three layers:

1. **In-process**: a single :class:`ControlRepository` exercises
   ``upsert → record_spawn → allocate_port → reconcile``.
2. **Concurrent**: two threads concurrently allocate ports to
   distinct runtimes to confirm ``BEGIN IMMEDIATE`` serialises
   writes correctly.
3. **Secret round-trip**: ``put_secret`` / ``verify_secret`` survive
   the salted hash boundary.

Per plan §15.1 ("Unit") these guard the contract before Phase 6's CLI
and Phase 7's multi-MAGI surface assume it.  Phase 9 acceptance
("Architecture") is verified by ``tests/architecture/test_import_boundaries.py``.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Iterable

import pytest

from magi.bus.db.control.engine import build_control_engine
from magi.bus.db.control.models import RuntimeDesiredState, RuntimeObservedState
from magi.bus.db.control.repository import (
    ControlRepository,
    PortAllocationDTO,
    PortAlreadyAllocated,
    RuntimeStateDTO,
    UnknownRuntime,
)


@pytest.fixture()
def repo(tmp_path: Path) -> ControlRepository:
    engine = build_control_engine(tmp_path / "control")
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
        backend_ref="local-adam",
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
    # Re-allocating the same runtime returns the SAME port.
    alloc2 = repo.allocate_port(7)
    assert alloc1.port == alloc2.port
    repo.release_port(7)
    # After release a fresh runtime picks the next-lowest free port
    # (still in range).
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
    engine = build_control_engine(Path("/tmp") / uuid.uuid4().hex)
    repo = ControlRepository(engine)
    with pytest.raises(UnknownRuntime):
        repo.get_runtime(99999)


def test_secrets_round_trip(repo: ControlRepository) -> None:
    repo.put_secret("control-plane", "s3cret-token-AAA")
    assert repo.verify_secret("control-plane", "s3cret-token-AAA")
    assert not repo.verify_secret("control-plane", "wrong-token")
    assert not repo.verify_secret("other-name", "s3cret-token-AAA")


def test_concurrent_port_allocations(tmp_path: Path) -> None:
    """Two threads concurrently allocate ports to distinct runtimes.

    The Local Profile is single-user per plan §6.3; this test runs on
    a developer workstation so contention is real (not synthetic).  The
    SQLAlchemy engine + WAL + ``BEGIN IMMEDIATE`` must serialise the
    writes — no duplicate ports, no missing rows.
    """
    engine = build_control_engine(tmp_path / "control-concurrent")
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
