from __future__ import annotations

import time

import pytest

from bus import (
    BaseWorker,
    CallLLMJob,
    CallLLMResult,
    JobStatus,
    LLMErrorCode,
    SlotTag,
)
from bus.firmware.jobs.callLLMJob import CallLLMJobBoard
from launcher import Launcher
from providers.requiredSlots import REQUIRED_SLOTS as PROVIDER_SLOTS
from providers.worker import ProvidersWorker
from tests.unit.new_bus.testing import attach_board


@pytest.fixture(autouse=True)
def _launcher_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("launcher.launcher.WORKSPACE_PATH", str(tmp_path))


_SHARED_LLM_SLOTS = (
    SlotTag(CallLLMJob, "publish"),
    SlotTag(CallLLMJob, "claim"),
    SlotTag(CallLLMJob, "submit_result"),
)


class SharedLLMWorker(BaseWorker):
    worker_name = "shared-one"
    required_slots = _SHARED_LLM_SLOTS


class GateWorker(BaseWorker):
    worker_name = "gate-one"
    required_slots = (SlotTag(CallLLMJob, "claim_post_result"),)


class SecondSharedLLMWorker(SharedLLMWorker):
    worker_name = "shared-two"


class SecondGateWorker(GateWorker):
    worker_name = "gate-two"


def _wait_result(board, job_id: int, *, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = board.get_result(job_id)
        if result is not None:
            return result
        time.sleep(0.05)
    return None


def test_provider_worker_loads_required_slots_from_package_file() -> None:
    assert ProvidersWorker.required_slots == PROVIDER_SLOTS
    assert ProvidersWorker.declared_slots() == PROVIDER_SLOTS


def test_launcher_launches_provider_worker() -> None:
    with Launcher() as launcher:
        assert launcher.run()
        worker = launcher.workers["providers"]
        assert isinstance(worker, ProvidersWorker)
        assert worker.is_alive()
        publisher = attach_board(
            launcher.bus,
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

        launcher.shutdown()
        assert not worker.is_alive()
        assert launcher.workers == {}


def test_launcher_attaches_workers_with_shared_slots(monkeypatch) -> None:
    monkeypatch.setattr(
        "launcher.launcher.WORKERS", (SharedLLMWorker, SecondSharedLLMWorker)
    )
    with Launcher() as launcher:
        assert launcher.run()
        workers = launcher.workers
        assert workers["shared-one"].is_alive()
        assert workers["shared-two"].is_alive()
        one = workers["shared-one"].bus
        two = workers["shared-two"].bus
        assert one is not None and two is not None
        job = CallLLMJob(messages=[{"role": "user", "content": "hi"}])
        job.id = one.board(CallLLMJob).publish(job)
        claimed = two.board(CallLLMJob).claim()
        assert claimed is not None
        assert one.board(CallLLMJob).submit_result(CallLLMResult(id=claimed.id))


def test_launcher_attaches_workers_with_shared_post_submit_slot(monkeypatch) -> None:
    monkeypatch.setattr("launcher.launcher.WORKERS", (GateWorker, SecondGateWorker))
    with Launcher() as launcher:
        assert launcher.run()
        assert launcher.workers["gate-one"].is_alive()
        assert launcher.workers["gate-two"].is_alive()


def test_run_rolls_back_when_a_worker_refuses(monkeypatch) -> None:
    class RefusingWorker(SecondSharedLLMWorker):
        def attach(self, _bus_for_worker) -> bool:
            return False

    monkeypatch.setattr("launcher.launcher.WORKERS", (SharedLLMWorker, RefusingWorker))
    with Launcher() as launcher:
        assert not launcher.run()
        assert launcher.workers == {}


def test_unknown_slot_does_not_run_workers(monkeypatch) -> None:
    class MissingSlotWorker(SharedLLMWorker):
        required_slots = (SlotTag(CallLLMJob, "missing"),)

    monkeypatch.setattr("launcher.launcher.WORKERS", (MissingSlotWorker,))
    with Launcher() as launcher:
        assert not launcher.run()


def test_duplicate_worker_id_is_rejected(monkeypatch) -> None:
    class One(SharedLLMWorker):
        worker_name = "same"

    class Two(SharedLLMWorker):
        worker_name = "same"

    monkeypatch.setattr("launcher.launcher.WORKERS", (One, Two))
    with Launcher() as launcher:
        with pytest.raises(ValueError, match="duplicate worker_id"):
            launcher.run()
