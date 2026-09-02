from __future__ import annotations

from pathlib import Path

import pytest

import bus.magi as bus_runtime
from bus import BaseWorker, Bus, ListSettingsJob


class FirstWorker(BaseWorker):
    worker_name = "first"


class SecondWorker(BaseWorker):
    worker_name = "second"


class CatalogWorker(BaseWorker):
    worker_name = "catalog"
    default_settings = {"theme": "dark", "locale": "en"}


def _listed(bus: Bus) -> dict[str, str]:
    board = bus.board(ListSettingsJob)
    result = board.publish(ListSettingsJob(publisher="test"))
    return result.settings


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
        bus.stop()
        assert bus.workers == {}
        assert not worker.is_alive()


def test_bus_rejects_duplicate_worker_name(tmp_path) -> None:
    class DuplicateWorker(FirstWorker):
        worker_name = "first"

    with Bus("@unit.magi", workspace=tmp_path / "workspace") as bus:
        assert bus.attach(FirstWorker)
        with pytest.raises(ValueError, match="duplicate worker_name"):
            bus.attach(DuplicateWorker)


def test_main_attaches_workers_and_serves(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(bus_runtime, "WORKERS", (FirstWorker, SecondWorker))
    seen: dict[str, object] = {}

    def fake_serve(self: Bus) -> None:
        seen["handle"] = self.handle
        seen["workers"] = set(self.workers)

    monkeypatch.setattr(Bus, "start", fake_serve)
    assert bus_runtime.main(["@alice.magi", "http://127.0.0.1:42069", "alice-token"]) == 0
    assert seen["handle"] == "@alice.magi"
    assert seen["workers"] == {"first", "second"}


def test_worker_boosts_defaults_without_persisting_attach_settings(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@unit.magi", workspace=workspace) as bus:
        assert bus.attach(CatalogWorker)
        listed = _listed(bus)
        assert listed["catalog.theme"] == "dark"
        assert listed["catalog.locale"] == "en"

    with Bus("@unit.magi", workspace=workspace) as bus:
        assert bus.attach(CatalogWorker, settings={"theme": "light"})
        listed = _listed(bus)
        assert listed["catalog.theme"] == "dark"
        assert listed["catalog.locale"] == "en"
