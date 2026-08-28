import pytest

from magi.launcher import Launcher, WorkerSpec, load_required_slots
from magi.launcher.demo import DemoWorker
from magi.new_bus import (
    AndDock,
    BaseJobResult,
    Bus,
    CreateConversationJob,
    OrDock,
    Slot,
)
from tests.unit.new_bus.testing import InMemoryBackend, PingBus, PingJob
from tests.unit.new_bus.workers.post_result import PostResultWorker
from tests.unit.new_bus.workers.shared_ping import SharedPingWorker


def test_demo_worker_loads_required_slots_from_package_file() -> None:
    slots = DemoWorker.declared_slots()
    assert slots == load_required_slots(DemoWorker)
    assert slots == (Slot(CreateConversationJob, "publish"),)


def test_launcher_docks_demo_workers_and_runs_them() -> None:
    with Bus(InMemoryBackend()) as bus, Launcher(bus) as launcher:
        workers = launcher.start(
            (
                WorkerSpec("conversation-a", DemoWorker),
                WorkerSpec("conversation-b", DemoWorker),
            )
        )
        assert workers is not None
        assert workers["conversation-a"].is_running
        assert workers["conversation-b"].is_alive()
        assert isinstance(bus._docks[Slot(CreateConversationJob, "publish")], OrDock)

        launcher.stop()
        assert not workers["conversation-a"].is_running
        assert launcher.workers == {}


def test_launcher_installs_or_docks_before_workers_attach() -> None:
    with PingBus(InMemoryBackend()) as bus, Launcher(bus) as launcher:
        workers = launcher.start(
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
    with PingBus(InMemoryBackend()) as bus, Launcher(bus) as launcher:
        workers = launcher.start(
            (
                WorkerSpec("one", PostResultWorker),
                WorkerSpec("two", PostResultWorker),
            )
        )
        assert workers is not None
        assert isinstance(bus._docks[Slot(PingJob, "submit_post_result")], AndDock)


def test_single_worker_does_not_install_a_dock() -> None:
    with PingBus(InMemoryBackend()) as bus, Launcher(bus) as launcher:
        workers = launcher.start((WorkerSpec("only", SharedPingWorker),))
        assert workers is not None
        assert Slot(PingJob, "publish") not in bus._docks


def test_start_rolls_back_when_a_worker_refuses() -> None:
    class RefusingWorker(SharedPingWorker):
        def on_start(self) -> bool:
            return False

    with PingBus(InMemoryBackend()) as bus, Launcher(bus) as launcher:
        workers = launcher.start(
            (
                WorkerSpec("one", SharedPingWorker),
                WorkerSpec("two", RefusingWorker),
            )
        )
        assert workers is None
        assert launcher.workers == {}


def test_unknown_slot_does_not_start_workers() -> None:
    class MissingSlotWorker(SharedPingWorker):
        required_slots = (Slot(PingJob, "missing"),)

    with PingBus(InMemoryBackend()) as bus, Launcher(bus) as launcher:
        assert launcher.start((WorkerSpec("ghost", MissingSlotWorker),)) is None


def test_duplicate_worker_id_is_rejected() -> None:
    with PingBus(InMemoryBackend()) as bus, Launcher(bus) as launcher:
        with pytest.raises(ValueError, match="duplicate worker_id"):
            launcher.start(
                (
                    WorkerSpec("one", SharedPingWorker),
                    WorkerSpec("one", SharedPingWorker),
                )
            )
