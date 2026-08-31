from __future__ import annotations

import time
from pathlib import Path

import pytest

import magi.__main__ as magi_cli
import magi.magi as magi_runtime
from bus import BaseWorker, CallLLMJob, CallLLMResult, JobStatus, go
from bus.firmware.jobs.callLLMJob import CallLLMJobBoard
from magi import Magi
from magi.constant import WORKERS, workspace_path
from providers.worker import ProvidersWorker
from tests.unit.new_bus.testing import attach_board, wait_publish


class SharedLLMWorker(BaseWorker):
    worker_name = "shared-one"


class SecondSharedLLMWorker(SharedLLMWorker):
    worker_name = "shared-two"


def _wait_result(board, job_id: int, *, timeout: float = 2.0):
    return board.get_result(job_id, timeout=timeout)


def _wait_claim(board, *, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        claimed = board.claim()
        if claimed is not None:
            return claimed
        time.sleep(0.05)
    return None


def _magi(handle: str, *, worker_types=None) -> Magi:
    kwargs = {}
    if worker_types is not None:
        kwargs["worker_types"] = worker_types
    return Magi(handle, **kwargs)


@pytest.fixture
def magi_handle(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(
        magi_runtime,
        "workspace_path",
        lambda handle: tmp_path / handle.lstrip("@") / "workspace",
    )
    return "@unit.magi"


def test_workspace_path_is_derived_from_handle() -> None:
    assert workspace_path("@alice.magi") == Path.home() / ".magi" / "alice.magi" / "workspace"


def test_main_starts_the_named_magi(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class StubMagi:
        def __init__(self, handle: str, *, worker_types) -> None:
            seen["handle"] = handle
            seen["worker_types"] = worker_types

        def serve(self) -> None:
            seen["served"] = True

    monkeypatch.setattr(magi_cli, "Magi", StubMagi)

    assert magi_cli.main(["@alice.magi", "http://127.0.0.1:42069", "alice-token"]) == 0
    assert seen["handle"] == "@alice.magi"
    assert len(seen["worker_types"]) == len(WORKERS) + 1
    assert seen["served"] is True


def test_magi_does_not_own_an_asp_client(magi_handle) -> None:
    with _magi(magi_handle) as magi:
        assert not hasattr(magi, "asp_client")


def test_magi_attaches_default_provider_worker(magi_handle) -> None:
    with _magi(magi_handle) as magi:
        assert magi.run()
        worker = magi.workers["providers"]
        assert isinstance(worker, ProvidersWorker)
        assert worker.is_alive()
        publisher = attach_board(magi.bus, CallLLMJobBoard)
        job = CallLLMJob(messages=[{"role": "user", "content": "hi"}])
        job.id = wait_publish(publisher, job)
        result = _wait_result(publisher, job.id)
        assert result is not None
        assert result.status is JobStatus.FAILED
        assert result.error

        magi.shutdown()
        assert not worker.is_alive()
        assert magi.workers == {}


def test_magi_workers_share_one_bus(magi_handle) -> None:
    with _magi(
        magi_handle,
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
        job.id = wait_publish(board, job)
        other = two.board(CallLLMJob)
        assert other is not None
        claimed = _wait_claim(other)
        assert claimed is not None
        assert go(board.submit_result(CallLLMResult(id=claimed.id))).result()


def test_magi_rolls_back_when_a_worker_refuses(magi_handle) -> None:
    class RefusingWorker(SecondSharedLLMWorker):
        def attach(self, _bus) -> bool:
            return False

    with _magi(
        magi_handle,
        worker_types=(SharedLLMWorker, RefusingWorker),
    ) as magi:
        assert not magi.run()
        assert magi.workers == {}


def test_magi_rejects_duplicate_worker_id(magi_handle) -> None:
    class One(SharedLLMWorker):
        worker_name = "same"

    class Two(SharedLLMWorker):
        worker_name = "same"

    with _magi(magi_handle, worker_types=(One, Two)) as magi:
        with pytest.raises(ValueError, match="duplicate worker_id"):
            magi.run()
