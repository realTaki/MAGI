from __future__ import annotations

from pathlib import Path

import pytest

import bus.magi as bus_runtime
from bus import BaseWorker, Bus
from bus.constant import WorkerConfig
from bus.magi import Magi


class FirstWorker(BaseWorker):
    worker_name = "first"


class SecondWorker(BaseWorker):
    worker_name = "second"


def test_workspace_is_derived_from_handle(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    with Bus("@alice.magi") as bus:
        assert bus.workspace == tmp_path / ".magi" / "alice.magi" / "workspace"


def test_bus_creates_and_attaches_worker_factories(tmp_path) -> None:
    with Bus("@unit.magi", workspace=tmp_path / "workspace") as bus:
        assert bus.attach(FirstWorker)
        worker = bus.workers["first"]
        assert worker.is_alive()
        assert worker.bus is bus
        bus.shutdown()
        assert bus.workers == {}
        assert not worker.is_alive()


def test_bus_rejects_duplicate_worker_name(tmp_path) -> None:
    class DuplicateWorker(FirstWorker):
        worker_name = "first"

    with Bus("@unit.magi", workspace=tmp_path / "workspace") as bus:
        assert bus.attach(FirstWorker)
        with pytest.raises(ValueError, match="duplicate worker_name"):
            bus.attach(DuplicateWorker)


def test_magi_attaches_workers_through_its_bus(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(bus_runtime, "WORKERS", (WorkerConfig(FirstWorker, {}), WorkerConfig(SecondWorker, {})))
    with Magi("@alice.magi", "http://127.0.0.1:42069", "alice-token") as magi:
        assert magi.run()
        assert {"first", "second"} == set(magi.bus.workers)


def test_main_starts_magi(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class StubMagi:
        def __init__(self, handle: str, base: str, token: str) -> None:
            seen["args"] = (handle, base, token)

        def serve(self) -> None:
            seen["served"] = True

    monkeypatch.setattr(bus_runtime, "Magi", StubMagi)

    assert bus_runtime.main(["@alice.magi", "http://127.0.0.1:42069", "alice-token"]) == 0
    assert seen["args"] == ("@alice.magi", "http://127.0.0.1:42069", "alice-token")
    assert seen["served"] is True
