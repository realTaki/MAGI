from __future__ import annotations

from datetime import timedelta

import pytest

from magi.new_bus import Bus, InvalidJobError
from magi.new_bus.base.time import utcnow
from magi.new_bus.testing import WORKER, PingJob


def test_other_worker_cannot_use_occupied_slot(bus: Bus) -> None:
    bus.publish(PingJob(), worker_id=WORKER)
    with pytest.raises(InvalidJobError, match="occupied"):
        bus.attach("other", PingJob, ("publish",))
    with pytest.raises(InvalidJobError, match="not held"):
        bus.claim(PingJob, worker_id="other")


def test_same_worker_reattach_renews(bus: Bus) -> None:
    bus.attach(WORKER, PingJob, ("publish",))
    bus.publish(PingJob(), worker_id=WORKER)


def test_heartbeat_keeps_lease(bus: Bus) -> None:
    bus.heartbeat(WORKER)
    bus.publish(PingJob(), worker_id=WORKER)


def test_expired_lease_can_be_taken(bus: Bus) -> None:
    past = utcnow() - timedelta(seconds=1)
    board = bus.job_board(PingJob)
    for name, holder in list(board._held.items()):
        if holder is not None:
            board._held[name] = (holder[0], past)
    bus.attach("other", PingJob, ("publish", "claim", "submit_result"))
    bus.publish(PingJob(), worker_id="other")
