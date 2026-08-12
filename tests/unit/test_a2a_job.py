"""MAGIS-shared A2A board and collaboration-directory coverage."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from magi.bus.db.base import utcnow_naive
from magi.bus.db.engine import EngineFactory
from magi.bus.db.schema import MAGIS_SCOPE, synchronise_schema
from magi.bus.guild.base import JobStatus
from magi.bus.guild.a2aJob import (
    A2AErrorCode,
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
            status=JobStatus.COMPLETED,
            content="The plan builds cleanly.",
        ),
    )
    result = requests.get_result(key=job_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED
    assert result.content == "The plan builds cleanly."
    assert result.error_code is None

    # A terminal request can never be overwritten by another response.
    requests.submit_result(
        key=job_id,
        result=A2ARequestResult(job_id=job_id, status=JobStatus.COMPLETED, content="different"),
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
    notifies.submit_result(key=job_id, result=A2ANotifyResult(job_id=job_id, status=JobStatus.COMPLETED))
    assert notifies.get_result(key=job_id).status == JobStatus.COMPLETED
    assert notifies.get_result(key=job_id).error_code is None


def test_route_is_scoped_to_one_magis_and_requests_expire(boards) -> None:
    source, target, memberships, requests, _notifies = boards
    with pytest.raises(ValueError, match="sending MAGI"):
        requests.publish(
            A2ARequestJob(
                source_magi_id=source.id,
                target_magi_id=source.id,
                text="self message",
            )
        )

    other_magis = MagisBook(requests._factory).add(name="Other")
    other_role = MagisRoleBook(requests._factory).add(magis_id=other_magis.id, name="EVA")
    other_member = memberships.add(magis_id=other_magis.id, role_id=other_role.id)
    with pytest.raises(ValueError, match="same MAGIS"):
        requests.publish(
            A2ARequestJob(
                source_magi_id=source.id,
                target_magi_id=other_member.id,
                text="cross society",
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
    assert result.status == JobStatus.FAILED
    assert result.error_code == "a2a_timeout"
    # Round-trip through the String(64) column must come back as the enum,
    # not a plain str — that's the whole point of the StrEnum migration.
    assert result.error_code is A2AErrorCode.TIMEOUT


def test_result_defaults_to_none_error_code() -> None:
    """``error_code`` defaults to ``None`` (no failure). The column's
    native ``Enum`` enforces the enum-membership constraint at the
    DB boundary, so dataclass construction itself is unvalidated
    (intentional — direct callers get no friction; bad values surface
    loudly at submit time via the CHECK constraint)."""
    assert A2ARequestResult().error_code is None
    assert A2ANotifyResult().error_code is None


def test_collaboration_directory_exposes_only_public_same_magis_members(boards) -> None:
    source, target, memberships, _requests, _notifies = boards
    directory = memberships.list_collaboration_directory(magi_id=source.id)
    assert [(item.magi_id, item.responsibility) for item in directory] == [
        (source.id, "Coordinates research and task decomposition."),
        (target.id, "Owns frontend implementation and build validation."),
    ]


def test_system_prompt_directory_includes_roles_and_responsibilities(boards) -> None:
    from magi.agent.system_prompt import _format_collaboration_directory

    source, _target, memberships, _requests, _notifies = boards
    block = _format_collaboration_directory(
        SimpleNamespace(memberships_book=memberships),  # type: ignore[arg-type]
        magi_id=source.id,
    )
    assert "MAGIS collaboration directory" in block
    assert "role: EVA" in block
    assert "Owns frontend implementation and build validation." in block


@pytest.mark.asyncio
async def test_message_magi_splits_request_and_notify_without_waiting_for_notify() -> None:
    from magi.agent.worker import AgentWorker, RunContext

    request_board = Mock()
    request_board.publish.return_value = "request-job"
    notify_board = Mock()
    notify_board.publish.return_value = "notify-job"
    bus = SimpleNamespace(
        a2a_request_job_board=request_board,
        a2a_notify_job_board=notify_board,
        tool_catalog_book=SimpleNamespace(get=lambda: None),
        tool_job_board=SimpleNamespace(publish=lambda _job: "tool-job"),
    )
    worker = AgentWorker(bus, magi_id=11)  # type: ignore[arg-type]

    async def direct_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    worker.call = direct_call  # type: ignore[method-assign]
    ctx = RunContext(contact_id=None, conversation_id="conv", channel="webui", caller_role=None)
    split = await worker._split_tools(
        ctx,
        [
            {
                "name": "message_magi",
                "id": "request-tool",
                "input": {"magi_id": 12, "mode": "request", "text": "Please review this."},
            },
            {
                "name": "message_magi",
                "id": "notify-tool",
                "input": {"magi_id": 13, "mode": "notify", "text": "FYI."},
            },
        ],
    )

    assert len(split.a2a_request_jobs) == 1
    assert len(split.a2a_notify_jobs) == 1
    _request_tc, request_job = split.a2a_request_jobs[0]
    assert request_job.source_magi_id == 11
    assert request_job.target_magi_id == 12

    _tool_ids, request_ids, notify_results = await worker._publish_effects(split)
    assert request_ids == {"request-tool": "request-job"}
    assert notify_results == {
        "notify-tool": {"success": True, "content": "A2A notification persisted for the target MAGI."}
    }
    request_board.publish.assert_called_once()
    notify_board.publish.assert_called_once()


@pytest.mark.asyncio
async def test_a2a_terminal_does_not_publish_human_delivery() -> None:
    from magi.agent.worker import AgentWorker, RunContext

    delivery_board = Mock()
    worker = AgentWorker(SimpleNamespace(delivery_job_board=delivery_board))  # type: ignore[arg-type]
    ctx = RunContext(
        contact_id=None,
        conversation_id="a2a.request:one",
        channel="a2a.request",
        caller_role=None,
        a2a_kind="a2a.request",
        final_reply="one response",
    )
    await worker._publish_delivery(ctx)
    delivery_board.publish.assert_not_called()


@pytest.mark.asyncio
async def test_agent_worker_completes_inbound_request_once_without_delivery() -> None:
    from magi.agent.worker import AgentWorker

    request_board = Mock()
    notify_board = Mock()
    delivery_board = Mock()
    bus = SimpleNamespace(
        a2a_request_job_board=request_board,
        a2a_notify_job_board=notify_board,
        delivery_job_board=delivery_board,
        settings_book=SimpleNamespace(get=lambda **_kwargs: None),
        agent_job_board=Mock(),
    )
    worker = AgentWorker(bus, magi_id=12)  # type: ignore[arg-type]
    job = A2ARequestJob(
        job_id="inbound-request",
        source_magi_id=11,
        target_magi_id=12,
        text="Please answer once.",
    )
    claims = [("a2a.request", job), (None, None)]

    async def direct_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def claim_next():
        return claims.pop(0)

    async def complete_once(ctx):
        ctx.final_reply = "One answer."
        worker._stopping = True

    worker.call = direct_call  # type: ignore[method-assign]
    worker._claim_next_turn = claim_next  # type: ignore[method-assign]
    worker._process = complete_once  # type: ignore[method-assign]
    await worker._run()

    result = request_board.submit_result.call_args.kwargs["result"]
    assert result.status == JobStatus.COMPLETED
    assert result.content == "One answer."
    delivery_board.publish.assert_not_called()


@pytest.mark.asyncio
async def test_target_agent_worker_consumes_shared_request_from_another_member(boards) -> None:
    from magi.agent.worker import AgentWorker

    source, target, _memberships, requests, notifies = boards
    request_id = requests.publish(
        A2ARequestJob(
            job_id="cross-member-request",
            source_magi_id=source.id,
            target_magi_id=target.id,
            text="Return one collaboration result.",
        )
    )
    delivery_board = Mock()
    worker = AgentWorker(
        SimpleNamespace(
            agent_job_board=SimpleNamespace(claim=lambda: None),
            a2a_request_job_board=requests,
            a2a_notify_job_board=notifies,
            delivery_job_board=delivery_board,
            settings_book=SimpleNamespace(get=lambda **_kwargs: None),
        ),
        magi_id=target.id,
    )  # type: ignore[arg-type]

    async def direct_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def complete_once(ctx):
        ctx.final_reply = "Collaboration completed."
        worker._stopping = True

    worker.call = direct_call  # type: ignore[method-assign]
    worker._process = complete_once  # type: ignore[method-assign]
    await worker._run()

    result = requests.get_result(key=request_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED
    assert result.content == "Collaboration completed."
    delivery_board.publish.assert_not_called()
