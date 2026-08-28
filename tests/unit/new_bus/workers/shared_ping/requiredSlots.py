"""Slots a shared Ping worker must hold before attach."""

from magi.new_bus import Slot
from tests.unit.new_bus.testing.jobs import PingJob

REQUIRED_SLOTS: tuple[Slot, ...] = (
    Slot(PingJob, "publish"),
    Slot(PingJob, "claim"),
    Slot(PingJob, "submit_result"),
)
