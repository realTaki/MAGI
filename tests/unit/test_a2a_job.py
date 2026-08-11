"""MAGIS-shared A2A board and collaboration-directory coverage."""

from __future__ import annotations

from datetime import timedelta

import pytest

from magi.bus.db.engine import EngineFactory
from magi.bus.db.schema import MAGIS_SCOPE, synchronise_schema
from magi.bus.db.base import utcnow_naive
from magi.bus.guild.a2aJob import (
    A2ANotifyJob,
    A2ANotifyResult,
    A2ARequestJob,
    A2ARequestResult,
    a2aNotifyBoard,
    a2aRequestJobBoard,
)
from magi.bus.library.magis.magisBook import MagisBook
from magi.bus.library.magis.membershipBook import MagisMembershipBook, MagisRoleBook


@pytest.fixture
def boards(tmp_path):
    factory = EngineFactory(f"sqlite:///{tmp_path / 'magis.db'}")
    synchronise_schema(factory, scope=MAGIS_SCOPE)
    magis = MagisBook(factory).add(name="Alpha")
    role = MagisRoleBook(factory).add(magis_id=magis.id, name="EVA")
    memberships = MagisMembershipBook(factory)
    source = memberships.add(
        magis_id=magis.id,
        role_id=role.id,
        responsibility="Coordinates research and task decomposition.",
    )
    target = memberships.add(
        magis_id=magis.id,
        role_id=role.id,
        responsibility="Owns frontend implementation and build validation.",
    )
    return (
        source,
        target,
        memberships,
        a2aRequestJobBoard(factory),
        a2aNotifyBoard(factory),
    )


def test_request_is_targeted_and_returns_one_durable_response(boards) -> None:
    source, target, _memberships, requests, _notifies = boards
    job_id = requests.publish(
        A2ARequestJob(
            job_id="request-one",
            source_magi_id=source.id,
            target_magi_id=target.id,
            tool_call_id="tool-one",
            text="Please validate the build plan.",
        )
    )

    assert requests.claim_for_target(magi_id=source.id) is None
    claimed = requests.claim_for_target(magi_id=target.id)
    assert claimed is not None
    assert claimed.job_id == job_id
    assert claimed.source_magi_id == source.id
    assert requests.get_result(key=job_id) is None

    requests.submit_result(
        key=job_id,
        result=A2ARequestResult(
            job_id=job_id,
            success=True,
            content="The plan builds cleanly.",
            tool_call_id="tool-one",
        ),
    )
    result = requests.get_result(key=job_id)
    assert result is not None
    assert result.success is True
    assert result.content == "The plan builds cleanly."

    # A terminal request can never be overwritten by another response.
    requests.submit_result(
        key=job_id,
        result=A2ARequestResult(job_id=job_id, success=True, content="different"),
    )
    assert requests.get_result(key=job_id).content == "The plan builds cleanly."


def test_notify_is_reliably_consumed_but_has_no_sender_wait_contract(boards) -> None:
    source, target, _memberships, _requests, notifies = boards
    job_id = notifies.publish(
        A2ANotifyJob(
            job_id="notify-one",
            source_magi_id=source.id,
            target_magi_id=target.id,
            text="Deployment has completed.",
        )
    )
    assert notifies.claim_for_target(magi_id=source.id) is None
    claimed = notifies.claim_for_target(magi_id=target.id)
    assert claimed is not None
    assert claimed.job_id == job_id
    notifies.submit_result(key=job_id, result=A2ANotifyResult(job_id=job_id, success=True))
    assert notifies.get_result(key=job_id).success is True


def test_route_is_scoped_to_one_magis_and_requests_expire(boards) -> None:
    source, target, _memberships, requests, _notifies = boards
    with pytest.raises(ValueError, match="sending MAGI"):
        requests.publish(
            A2ARequestJob(
                source_magi_id=source.id,
                target_magi_id=source.id,
                text="self message",
            )
        )

    expired_id = requests.publish(
        A2ARequestJob(
            job_id="expired-request",
            source_magi_id=source.id,
            target_magi_id=target.id,
            text="too late",
            deadline_at=utcnow_naive() - timedelta(seconds=1),
        )
    )
    assert requests.claim_for_target(magi_id=target.id) is None
    result = requests.get_result(key=expired_id)
    assert result is not None
    assert result.success is False
    assert result.error_code == "a2a_timeout"


def test_collaboration_directory_exposes_only_public_same_magis_members(boards) -> None:
    source, target, memberships, _requests, _notifies = boards
    directory = memberships.list_collaboration_directory(magi_id=source.id)
    assert [(item.magi_id, item.responsibility) for item in directory] == [
        (source.id, "Coordinates research and task decomposition."),
        (target.id, "Owns frontend implementation and build validation."),
    ]
