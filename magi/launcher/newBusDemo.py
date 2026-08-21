"""Run ``python -m magi.launcher.newBusDemo`` for a minimal Dock launch."""

from __future__ import annotations

from magi.new_bus import Bus, JobBoardClient, SQLiteBackend, WorkerBus, job_board
from magi.new_bus.firmware.jobs.conversationJobs import (
    CreateConversationJob,
    CreateConversationJobBoard,
    CreateConversationResult,
)

from .newBus import Launcher, WorkerSpec


class ConversationWorkerBus(WorkerBus):
    createConversationJobBoard: JobBoardClient[CreateConversationJob, CreateConversationResult] = (
        job_board(
            CreateConversationJobBoard,
            slots=("publish",),
        )
    )


def main() -> int:
    with Bus(SQLiteBackend(memory=True)) as bus:
        workers = Launcher(bus).start(
            (
                WorkerSpec("conversation-a", ConversationWorkerBus),
                WorkerSpec("conversation-b", ConversationWorkerBus),
            )
        )
        if workers is None:
            return 1
        job_id = workers["conversation-a"].createConversationJobBoard.publish(
            CreateConversationJob(delivery_address="demo", contact_id=1, channel="demo")
        )
        return 0 if job_id else 1


if __name__ == "__main__":
    raise SystemExit(main())
