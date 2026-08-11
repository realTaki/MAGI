"""Persistence-level delivery job board regression tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from magi.bus.db import EngineFactory
from magi.bus.db.base import utcnow_naive
from magi.bus.guild.deliveryJob import DeliveryJob, _DeliveryJobRow, deliveryJobBoard


@pytest.fixture
def board(tmp_path) -> deliveryJobBoard:
    factory = EngineFactory(f"sqlite:///{tmp_path / 'delivery.sqlite'}")
    factory.create_all()
    return deliveryJobBoard(factory)


def test_claim_for_channel_never_claims_another_channel(board: deliveryJobBoard) -> None:
    tg_job_id = board.publish(DeliveryJob(channel="tg", payload={"text": "tg"}))
    webui_job_id = board.publish(DeliveryJob(channel="webui", payload={"text": "webui"}))

    webui_claim = board.claim_for_channel(channel="webui")
    assert webui_claim is not None
    assert webui_claim.job_id == webui_job_id
    assert webui_claim.channel == "webui"

    tg_claim = board.claim_for_channel(channel="tg")
    assert tg_claim is not None
    assert tg_claim.job_id == tg_job_id
    assert tg_claim.channel == "tg"


def test_concurrent_channel_consumers_claim_a_job_once(board: deliveryJobBoard) -> None:
    board.publish(DeliveryJob(channel="webui", payload={"text": "once"}))
    other_consumer = deliveryJobBoard(board._factory)
    barrier = Barrier(2)

    def claim(candidate: deliveryJobBoard):
        barrier.wait()
        return candidate.claim_for_channel(channel="webui")

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, (board, other_consumer)))

    assert sum(job is not None for job in claims) == 1


def test_channel_claim_exhaustion_uses_shared_retry_policy(
    board: deliveryJobBoard,
) -> None:
    job_id = board.publish(DeliveryJob(channel="webui", payload={"text": "retry"}))

    for _ in range(board.max_attempts):
        claim = board.claim_for_channel(channel="webui")
        assert claim is not None
        with board._session() as session:
            row = session.scalar(select(_DeliveryJobRow).where(_DeliveryJobRow.job_id == job_id))
            assert row is not None
            row.leased_until = utcnow_naive() - timedelta(seconds=1)
            session.commit()

    assert board.claim_for_channel(channel="webui") is None
    result = board.get_result(key=job_id)
    assert result is not None
    assert result.success is False
    assert result.error == f"job exhausted after {board.max_attempts} attempt(s)"
