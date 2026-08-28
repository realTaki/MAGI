from __future__ import annotations

import time

import pytest

from magi.launcher import Launcher, WorkerSpec, default_specs
from magi.new_bus import (
    AndDock,
    BaseJobResult,
    Bus,
    CallLLMJob,
    JobStatus,
    LLMErrorCode,
    OrDock,
    Slot,
)
from magi.new_bus.firmware.jobs.callLLMJob import CallLLMJobBoard
from magi.providers.requiredSlots import REQUIRED_SLOTS as PROVIDER_SLOTS
from magi.providers.worker import ProvidersWorker
from tests.unit.new_bus.testing import InMemoryBackend, PingBus, PingJob, attach_board
from tests.unit.new_bus.workers.post_result import PostResultWorker
from tests.unit.new_bus.workers.shared_ping import SharedPingWorker


def _wait_result(board, job_id: int, *, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = board.get_result(job_id)
        if result is not None:
            return result
        time.sleep(0.05)
    return None


def test_provider_worker_loads_required_slots_from_package_file() -> None:
    slots = ProvidersWorker.declared_slots()
    assert slots == ProvidersWorker.load_required_slots()
    assert slots == PROVIDER_SLOTS


def test_launcher_attaches_provider_worker() -> None:
    with Bus(InMemoryBackend()) as bus, Launcher.for_bus(bus) as launcher:
        workers = launcher.attach(default_specs())
        assert workers is not None
        worker = workers["providers"]
        assert isinstance(worker, ProvidersWorker)
        assert worker.is_attached()
        assert worker.is_alive()
        for slot in PROVIDER_SLOTS:
            assert slot not in bus._docks

        publisher = attach_board(
            bus,
            CallLLMJobBoard,
            worker_id="caller",
            slots=("publish",),
        )
        job = CallLLMJob(messages=[{"role": "user", "content": "hi"}])
        job.id = publisher.publish(job)
        result = _wait_result(publisher, job.id)
        assert result is not None
        assert result.status is JobStatus.FAILED
        assert result.error_code in {
            LLMErrorCode.CREDENTIALS_REQUIRED,
            LLMErrorCode.UNKNOWN,
        }

        launcher.detach()
        assert not worker.is_attached()
        assert launcher.workers == {}


def test_launcher_installs_or_docks_before_workers_attach() -> None:
    with PingBus(InMemoryBackend()) as bus, Launcher.for_bus(bus) as launcher:
        workers = launcher.attach(
            (
                WorkerSpec("one", SharedPingWorker),
                WorkerSpec("two", SharedPingWorker),
            )
        )
        assert workers is not None
        assert workers["one"].is_alive()
        assert workers["two"].is_alive()
        for name in ("publish", "claim", "submit_result"):
            assert isinstance(bus._docks[Slot(PingJob, name)], OrDock)

        one = workers["one"].bus
        two = workers["two"].bus
        assert one is not None and two is not None
        job = PingJob(n=1)
        job.id = one.board(PingJob).publish(job)
        claimed = two.board(PingJob).claim()
        assert claimed is not None
        assert one.board(PingJob).submit_result(BaseJobResult(id=claimed.id))


def test_launcher_selects_and_dock_for_post_submit_slots() -> None:
    with PingBus(InMemoryBackend()) as bus, Launcher.for_bus(bus) as launcher:
        workers = launcher.attach(
            (
                WorkerSpec("one", PostResultWorker),
                WorkerSpec("two", PostResultWorker),
            )
        )
        assert workers is not None
        assert isinstance(bus._docks[Slot(PingJob, "submit_post_result")], AndDock)


def test_single_worker_does_not_install_a_dock() -> None:
    with PingBus(InMemoryBackend()) as bus, Launcher.for_bus(bus) as launcher:
        workers = launcher.attach((WorkerSpec("only", SharedPingWorker),))
        assert workers is not None
        assert Slot(PingJob, "publish") not in bus._docks


def test_attach_rolls_back_when_a_worker_refuses() -> None:
    class RefusingWorker(SharedPingWorker):
        def attach(self, _bus_for_worker) -> bool:
            return False

    with PingBus(InMemoryBackend()) as bus, Launcher.for_bus(bus) as launcher:
        workers = launcher.attach(
            (
                WorkerSpec("one", SharedPingWorker),
                WorkerSpec("two", RefusingWorker),
            )
        )
        assert workers is None
        assert launcher.workers == {}


def test_unknown_slot_does_not_attach_workers() -> None:
    class MissingSlotWorker(SharedPingWorker):
        required_slots = (Slot(PingJob, "missing"),)

    with PingBus(InMemoryBackend()) as bus, Launcher.for_bus(bus) as launcher:
        assert launcher.attach((WorkerSpec("ghost", MissingSlotWorker),)) is None


def test_duplicate_worker_id_is_rejected() -> None:
    with PingBus(InMemoryBackend()) as bus, Launcher.for_bus(bus) as launcher:
        with pytest.raises(ValueError, match="duplicate worker_id"):
            launcher.attach(
                (
                    WorkerSpec("one", SharedPingWorker),
                    WorkerSpec("one", SharedPingWorker),
                )
            )
