from __future__ import annotations

from magi.launcher.newBus import Launcher, WorkerSpec
from magi.new_bus import AndDock, BaseJobResult, Bus, JobBoardClient, Slot, WorkerBus, job_board
from tests.unit.new_bus.testing import InMemoryBackend, PingJob, PingJobBoard


class SharedPingWorkerBus(WorkerBus):
    pingJobBoard: JobBoardClient[PingJob, BaseJobResult] = job_board(
        PingJobBoard,
        slots=("publish", "claim", "submit_result"),
    )


class PostResultWorkerBus(WorkerBus):
    pingJobBoard: JobBoardClient[PingJob, BaseJobResult] = job_board(
        PingJobBoard,
        slots=("submit_post_result",),
    )


def test_launcher_installs_or_docks_before_workers_attach() -> None:
    with Bus(InMemoryBackend()) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        workers = Launcher(bus).start(
            (
                WorkerSpec("one", SharedPingWorkerBus),
                WorkerSpec("two", SharedPingWorkerBus),
            )
        )
        assert workers is not None
        assert workers["one"].is_alive()
        assert workers["two"].is_alive()

        job = PingJob(n=1)
        job.id = workers["one"].pingJobBoard.publish(job)
        claimed = workers["two"].pingJobBoard.claim()
        assert claimed is not None
        assert workers["one"].pingJobBoard.submit_result(BaseJobResult(id=claimed.id))


def test_launcher_selects_and_dock_for_post_submit_slots() -> None:
    with Bus(InMemoryBackend()) as bus:
        bus.mount_job(PingJob, board_cls=PingJobBoard)
        workers = Launcher(bus).start(
            (
                WorkerSpec("one", PostResultWorkerBus),
                WorkerSpec("two", PostResultWorkerBus),
            )
        )
        assert workers is not None
        assert isinstance(bus._docks[Slot(PingJob, "submit_post_result")], AndDock)
