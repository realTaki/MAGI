from __future__ import annotations

from magi.launcher.newBus import Launcher, WorkerSpec
from magi.new_bus import BaseJobResult, Bus, JobBoardClient, WorkerBus, job_board
from magi.new_bus.testing import InMemoryBackend, PingJob, PingJobBoard


class SharedPingWorkerBus(WorkerBus):
    pingJobBoard: JobBoardClient[PingJob, BaseJobResult] = job_board(
        PingJobBoard,
        slots=("publish", "claim", "submit_result"),
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
