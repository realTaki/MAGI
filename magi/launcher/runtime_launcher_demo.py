"""Run ``python -m magi.launcher.runtime_launcher_demo`` for a Dock demo."""

from __future__ import annotations

from dataclasses import dataclass, field

from magi.new_bus import BusForWorker, Slot, SQLiteBackend
from magi.new_bus.firmware.jobs.conversationJobs import CreateConversationJob

from .runtime_launcher import RuntimeLauncher, WorkerLaunchSpec


@dataclass
class ConversationWorker:
    bus: BusForWorker | None = field(init=False, default=None)

    def attach(self, bus_for_worker: BusForWorker) -> None:
        self.bus = bus_for_worker


def main() -> int:
    launcher = RuntimeLauncher(SQLiteBackend(memory=True))
    try:
        workers = launcher.start(
            (
                WorkerLaunchSpec(
                    "conversation-a",
                    (Slot(CreateConversationJob, "publish"),),
                    ConversationWorker,
                ),
                WorkerLaunchSpec(
                    "conversation-b",
                    (Slot(CreateConversationJob, "publish"),),
                    ConversationWorker,
                ),
            )
        )
        if workers is None:
            return 1
        worker = workers["conversation-a"]
        assert isinstance(worker, ConversationWorker) and worker.bus is not None
        job_id = worker.bus.board(CreateConversationJob).publish(
            CreateConversationJob(delivery_address="demo", contact_id=1, channel="demo")
        )
        return 0 if job_id else 1
    finally:
        launcher.close()


if __name__ == "__main__":
    raise SystemExit(main())
