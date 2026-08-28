from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from magi.new_bus import Bus
from tests.unit.new_bus.testing import WORKER, PingBus, PingJobBoard, attach_board


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def bus(workspace: Path) -> Iterator[PingBus]:
    with PingBus(workspace) as item:
        yield item


@pytest.fixture
def ping_board(bus: Bus):
    return attach_board(
        bus,
        PingJobBoard,
        worker_id=WORKER,
        slots=("publish", "claim", "submit_result"),
    )
