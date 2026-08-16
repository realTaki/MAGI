"""Persistence-level delivery job board regression tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from magi.bus.db import EngineFactory
from magi.bus.db.base import utcnow_naive
from magi.bus.db.schema import LOCAL_SCOPE, synchronise_schema
from magi.bus.guild.base import JobStatus
from magi.bus.guild.deliveryJob import DeliveryJob, _DeliveryJobRow, deliveryJobBoard


@pytest.fixture
def board(tmp_path) -> deliveryJobBoard:
    factory = EngineFactory(f"sqlite:///{tmp_path / 'delivery.sqlite'}")
    synchronise_schema(factory, scope=LOCAL_SCOPE)
    return deliveryJobBoard(factory, lease_seconds=60)


def test_claim_for_channel_never_claims_another_channel(board: deliveryJobBoard) -> None:
    tg_job_id = board.publish(DeliveryJob(channel="tg", text="tg"))
    webui_job_id = board.publish(DeliveryJob(channel="webui", text="webui"))

    webui_claim = board.claim_for_channel(channel="webui", worker_id="webui-worker")
    assert webui_claim is not None
    assert webui_claim.job_id == webui_job_id
    assert webui_claim.channel == "webui"

    tg_claim = board.claim_for_channel(channel="tg", worker_id="tg-worker")
    assert tg_claim is not None
    assert tg_claim.job_id == tg_job_id
    assert tg_claim.channel == "tg"


def test_concurrent_channel_consumers_claim_a_job_once(board: deliveryJobBoard) -> None:
    board.publish(DeliveryJob(channel="webui", text="once"))
    other_consumer = deliveryJobBoard(board._factory, lease_seconds=60)
    barrier = Barrier(2)

    def claim(candidate: deliveryJobBoard):
        barrier.wait()
        return candidate.claim_for_channel(channel="webui", worker_id=f"worker-{id(candidate)}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, (board, other_consumer)))

    assert sum(job is not None for job in claims) == 1


def test_channel_lease_recovery_never_auto_fails(
    board: deliveryJobBoard,
) -> None:
    job_id = board.publish(DeliveryJob(channel="webui", text="retry"))

    for worker_id in ("worker-a", "worker-b", "worker-c", "worker-d"):
        claim = board.claim_for_channel(channel="webui", worker_id=worker_id)
        assert claim is not None
        with board._session() as session:
            row = session.scalar(select(_DeliveryJobRow).where(_DeliveryJobRow.job_id == job_id))
            assert row is not None
            row.leased_until = utcnow_naive() - timedelta(seconds=1)
            session.commit()

    assert board.get_result(job_id=job_id) is None
