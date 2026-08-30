from __future__ import annotations

import socket
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import magi.service as magi_service
from bus import BaseWorker, CallLLMJob, CallLLMResult, JobStatus
from bus.firmware.jobs.callLLMJob import CallLLMJobBoard
from magi import Magi
from magi.constant import workspace_path
from providers.worker import ProvidersWorker
from tests.unit.new_bus.testing import attach_board


class SharedLLMWorker(BaseWorker):
    worker_name = "shared-one"


class SecondSharedLLMWorker(SharedLLMWorker):
    worker_name = "shared-two"


def _wait_result(board, job_id: int, *, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = board.get_result(job_id)
        if result is not None:
            return result
        time.sleep(0.05)
    return None


def _wait_claim(board, *, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        claimed = board.claim()
        if claimed is not None:
            return claimed
        time.sleep(0.05)
    return None


@pytest.fixture
def magi_name(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(magi_service, "workspace_path", lambda name: tmp_path / name / "workspace")
    return "unit"


def test_workspace_path_is_derived_from_magi_name() -> None:
    assert workspace_path("alice") == Path.home() / ".magi" / "alice" / "workspace"


def test_magi_attaches_default_provider_worker(magi_name) -> None:
    with Magi(magi_name) as magi:
        assert magi.run()
        worker = magi.workers["providers"]
        assert isinstance(worker, ProvidersWorker)
        assert worker.is_alive()
        publisher = attach_board(magi.bus, CallLLMJobBoard)
        job = CallLLMJob(messages=[{"role": "user", "content": "hi"}])
        job.id = publisher.publish(job)
        result = _wait_result(publisher, job.id)
        assert result is not None
        assert result.status is JobStatus.FAILED
        assert result.error

        magi.shutdown()
        assert not worker.is_alive()
        assert magi.workers == {}


def test_magi_workers_share_one_bus(magi_name) -> None:
    with Magi(
        magi_name,
        worker_types=(SharedLLMWorker, SecondSharedLLMWorker),
    ) as magi:
        assert magi.run()
        workers = magi.workers
        assert workers["shared-one"].is_alive()
        assert workers["shared-two"].is_alive()
        one = workers["shared-one"].bus
        two = workers["shared-two"].bus
        assert one is magi.bus
        assert two is magi.bus
        job = CallLLMJob(messages=[{"role": "user", "content": "hi"}])
        board = one.board(CallLLMJob)
        assert board is not None
        job.id = board.publish(job)
        other = two.board(CallLLMJob)
        assert other is not None
        claimed = _wait_claim(other)
        assert claimed is not None
        assert board.submit_result(CallLLMResult(id=claimed.id))


def test_magi_rolls_back_when_a_worker_refuses(magi_name) -> None:
    class RefusingWorker(SecondSharedLLMWorker):
        def attach(self, _bus) -> bool:
            return False

    with Magi(
        magi_name,
        worker_types=(SharedLLMWorker, RefusingWorker),
    ) as magi:
        assert not magi.run()
        assert magi.workers == {}


def test_magi_rejects_duplicate_worker_id(magi_name) -> None:
    class One(SharedLLMWorker):
        worker_name = "same"

    class Two(SharedLLMWorker):
        worker_name = "same"

    with Magi(magi_name, worker_types=(One, Two)) as magi:
        with pytest.raises(ValueError, match="duplicate worker_id"):
            magi.run()


def test_asgi_lifespan_attaches_workers_before_serving_api(magi_name) -> None:
    service = Magi(magi_name, worker_types=(SharedLLMWorker,))
    with TestClient(service.app) as client:
        assert service.workers["shared-one"].is_alive()
        assert client.get("/health").json() == {"status": "ok"}
    assert service.workers == {}


def test_magi_reserves_the_next_local_port(monkeypatch) -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    start = int(occupied.getsockname()[1])
    monkeypatch.setattr(magi_service, "FIRST_PORT", start)
    try:
        listener = magi_service._reserve_local_port()
    finally:
        occupied.close()
    try:
        assert listener.getsockname()[0] == "127.0.0.1"
        assert listener.getsockname()[1] > start
    finally:
        listener.close()
