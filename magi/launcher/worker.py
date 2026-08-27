"""Worker contract owned by the runtime composition layer."""

from __future__ import annotations

from typing import Protocol

from magi.new_bus import BusForWorker


class Worker(Protocol):
    """A runtime component that accepts its BUS slice after construction."""

    def attach(self, bus_for_worker: BusForWorker) -> None: ...
