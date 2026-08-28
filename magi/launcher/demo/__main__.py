"""Run ``python -m magi.launcher.demo`` for a Dock + lifecycle demo."""

from magi.launcher import Launcher, WorkerSpec
from magi.launcher.demo import DemoWorker
from magi.new_bus import Bus, CreateConversationJob, Slot, SQLiteBackend


def main() -> int:
    with Bus(SQLiteBackend(memory=True)) as bus, Launcher(bus) as launcher:
        workers = launcher.start(
            (
                WorkerSpec("conversation-a", DemoWorker),
                WorkerSpec("conversation-b", DemoWorker),
            )
        )
        if workers is None:
            return 1
        slot = Slot(CreateConversationJob, "publish")
        dock = bus._docks.get(slot)
        if dock is None or not workers["conversation-a"].is_running:
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
