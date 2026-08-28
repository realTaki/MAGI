"""Slots a post-result reviewer must hold before attach."""

from magi.new_bus import Slot
from tests.unit.new_bus.testing.jobs import PingJob

REQUIRED_SLOTS: tuple[Slot, ...] = (Slot(PingJob, "submit_post_result"),)
