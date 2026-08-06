"""Regression coverage for the transient control-job queue.

The ``control_jobs`` table is a signal queue -- the provider worker
drains ``provider.config_changed`` rows on every poll tick and
deletes them as part of the drain. ``llm_attempts`` and
``hook_evaluations`` own the durable trace; this test file pins the
drain contract.
"""

from __future__ import annotations

import pytest

from magi.bus.bootstrap import bootstrap
from magi.bus.db import init_orm
from magi.bus.db.engine import open_session
from magi.bus.db.magis.engine import init_magis_public_db
from magi.bus.db.models.queue import ControlJob
from magi.bus.protocols.control_jobs import PROVIDER_CONFIG_CHANGED


@pytest.fixture()
def bus_store(tmp_path, monkeypatch):
    """Stand up the private SQLite + magis engine.

    Mirrors ``tests/integration/test_providers_worker.py::magi_state``
    (sets ``MAGI_DATA_ROOT`` + ``HOST_WORKSPACE_DIR`` so the runtime
    bootstrap path resolves to a per-test directory) and returns
    ``get_bus_store()`` so writes go through the registered singleton.
    """
    from magi.bus import get_bus_store as _get_bus_store

    monkeypatch.setenv("MAGI_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HOST_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "MAGIS_DATABASE_URL", f"sqlite:///{tmp_path / 'magis.db'}",
    )
    init_orm(seed_root=True)
    init_magis_public_db(seed_root=True)
    bootstrap(initialise_local=True)
    return _get_bus_store()


def test_enqueue_and_drain_round_trip(bus_store) -> None:
    job_id = bus_store.enqueue_control_job(
        kind=PROVIDER_CONFIG_CHANGED,
        payload={"provider": "openai"},
    )
    with open_session(bus_store._state_dir) as session:
        assert session.query(ControlJob).filter_by(job_id=job_id).one()

    drained = bus_store.drain_control_jobs(
        worker_id="provider-test", kind=PROVIDER_CONFIG_CHANGED,
    )
    assert drained == 1

    # Rows are deleted by the drain (transient signal, not audit).
    with open_session(bus_store._state_dir) as session:
        assert session.query(ControlJob).count() == 0


def test_drain_returns_zero_when_queue_empty(bus_store) -> None:
    drained = bus_store.drain_control_jobs(
        worker_id="provider-test", kind=PROVIDER_CONFIG_CHANGED,
    )
    assert drained == 0


def test_drain_coalesces_many_rows_into_one_count(bus_store) -> None:
    """Three writes → one drain call returning 3.

    The worker treats any non-zero count as "rebuild once"; the
    number of queued rows is not propagated to its behaviour.
    """
    for _ in range(3):
        bus_store.enqueue_control_job(
            kind=PROVIDER_CONFIG_CHANGED,
            payload=None,
        )
    drained = bus_store.drain_control_jobs(
        worker_id="provider-test", kind=PROVIDER_CONFIG_CHANGED,
    )
    assert drained == 3


def test_drain_filters_by_kind(bus_store) -> None:
    """A future kind stays in the queue until a consumer drains it."""
    bus_store.enqueue_control_job(
        kind=PROVIDER_CONFIG_CHANGED,
        payload={"provider": "openai"},
    )
    bus_store.enqueue_control_job(
        kind="some.future.kind",
        payload={"x": 1},
    )

    drained = bus_store.drain_control_jobs(
        worker_id="provider-test", kind=PROVIDER_CONFIG_CHANGED,
    )
    assert drained == 1

    with open_session(bus_store._state_dir) as session:
        rows = session.query(ControlJob).all()
        assert [r.kind for r in rows] == ["some.future.kind"]