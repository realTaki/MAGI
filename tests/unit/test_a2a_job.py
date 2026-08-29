"""MAGIS-shared A2A board and collaboration-directory coverage."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from magi.old_bus.bases.db.base import utcnow_naive
from magi.old_bus.bases.db.engine import EngineFactory
from magi.old_bus.firmwares.schema import LOCAL_SCOPE, MAGIS_SCOPE, synchronise_schema
from magi.old_bus.firmwares.jobs.a2aJob import (
    A2ANotifyJob,
    A2ANotifyResult,
    A2ARequestJob,
    A2ARequestResult,
    a2aNotifyBoard,
    a2aRequestJobBoard,
)
from magi.old_bus.bases.job import JobStatus
from magi.old_bus.firmwares.books.local.conversationBook import ConversationBook, MessageBook
from magi.old_bus.firmwares.books.magis.magisBook import Magis, MagisBook
from magi.old_bus.firmwares.books.magis.membershipBook import (
    MagisMembership,
    MagisMembershipBook,
    MagisRole,
    MagisRoleBook,
)


@pytest.fixture
def boards(tmp_path):
    factory = EngineFactory(f"sqlite:///{tmp_path / 'magis.db'}")
    synchronise_schema(factory, scope=MAGIS_SCOPE)
    magis = MagisBook(factory).get(MagisBook(factory).add(Magis(name='Alpha')))
    role = MagisRoleBook(factory).get(MagisRoleBook(factory).add(MagisRole(magis_id=magis.id, name='EVA')))
    memberships = MagisMembershipBook(factory)
    source = memberships.get(memberships.add(MagisMembership(magis_id=magis.id, role_id=role.id, responsibility='Coordinates research and task decomposition.')))
    target = memberships.get(memberships.add(MagisMembership(magis_id=magis.id, role_id=role.id, responsibility='Owns frontend implementation and build validation.')))
    return (
        source,
        target,
        memberships,
        a2aRequestJobBoard(factory, memberships_book=memberships),
        a2aNotifyBoard(factory, memberships_book=memberships),
    )


@pytest.fixture
def transcript_boards(tmp_path):
    """One shared MAGIS queue with independent source/target local Books."""
    magis_factory = EngineFactory(f"sqlite:///{tmp_path / 'magis.db'}")
    synchronise_schema(magis_factory, scope=MAGIS_SCOPE)
    magis = MagisBook(magis_factory).get(MagisBook(magis_factory).add(Magis(name='Alpha')))
    role = MagisRoleBook(magis_factory).get(MagisRoleBook(magis_factory).add(MagisRole(magis_id=magis.id, name='EVA')))
    memberships = MagisMembershipBook(magis_factory)
    source = memberships.get(memberships.add(MagisMembership(magis_id=magis.id, role_id=role.id)))
    target = memberships.get(memberships.add(MagisMembership(magis_id=magis.id, role_id=role.id)))

    source_factory = EngineFactory(f"sqlite:///{tmp_path / 'source-local.db'}")
    target_factory = EngineFactory(f"sqlite:///{tmp_path / 'target-local.db'}")
    synchronise_schema(source_factory, scope=LOCAL_SCOPE)
    synchronise_schema(target_factory, scope=LOCAL_SCOPE)

    source_conversations = ConversationBook(source_factory)
    source_messages = MessageBook(source_factory)
    target_conversations = ConversationBook(target_factory)
    target_messages = MessageBook(target_factory)

    source_requests = a2aRequestJobBoard(
        magis_factory,
        memberships_book=memberships,
        messages_book=source_messages,
        conversations_book=source_conversations,
    )
    target_requests = a2aRequestJobBoard(
        magis_factory,
        memberships_book=memberships,
        messages_book=target_messages,
        conversations_book=target_conversations,
    )
    source_notifies = a2aNotifyBoard(
        magis_factory,
        memberships_book=memberships,
        messages_book=source_messages,
        conversations_book=source_conversations,
    )
    target_notifies = a2aNotifyBoard(
        magis_factory,
        memberships_book=memberships,
        messages_book=target_messages,
        conversations_book=target_conversations,
    )
    return SimpleNamespace(
        source=source,
        target=target,
        source_requests=source_requests,
        target_requests=target_requests,
        source_notifies=source_notifies,
        target_notifies=target_notifies,
        source_conversations=source_conversations,
        target_conversations=target_conversations,
        source_messages=source_messages,
        target_messages=target_messages,
    )


def _peer_messages(conversations, messages, *, peer_magi_id: int):
    conversation = conversations.get_or_create_for_a2a_peer(peer_magi_id=peer_magi_id)
    return messages.list_for_conversation(conversation_id=conversation.id)


def test_request_lifecycle_writes_each_executing_magi_message_book(transcript_boards) -> None:
    boards = transcript_boards
    job_id = boards.source_requests.publish(
        A2ARequestJob(
            source_magi_id=boards.source.id,
            target_magi_id=boards.target.id,
            text="Please review the integration.",
        )
    )
    assert [(m.role, m.text) for m in _peer_messages(
        boards.source_conversations,
        boards.source_messages,
        peer_magi_id=boards.target.id,
    )] == [("assistant", "Please review the integration.")]
    assert _peer_messages(
        boards.target_conversations,
        boards.target_messages,
        peer_magi_id=boards.source.id,
    ) == []

    claimed = boards.target_requests.claim_for_target(
        magi_id=boards.target.id, worker_id="target-worker"
    )
    assert claimed is not None
    assert [(m.role, m.text) for m in _peer_messages(
        boards.target_conversations,
        boards.target_messages,
        peer_magi_id=boards.source.id,
    )] == [("user", "Please review the integration.")]

    boards.target_requests.submit_result(
        job_id=job_id,
        worker_id="target-worker",
        result=A2ARequestResult(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            content="Integration is sound.",
        ),
    )
    assert [(m.role, m.text) for m in _peer_messages(
        boards.target_conversations,
        boards.target_messages,
        peer_magi_id=boards.source.id,
    )] == [
        ("user", "Please review the integration."),
        ("assistant", "Integration is sound."),
    ]

    result = boards.source_requests.get_result(job_id=job_id)
    assert result is not None
    assert result.content == "Integration is sound."
    # Repeated polling observes the same durable result but does not repeat
    # the source-side transcript event.
    assert boards.source_requests.get_result(job_id=job_id) is not None
    assert [(m.role, m.text) for m in _peer_messages(
        boards.source_conversations,
        boards.source_messages,
        peer_magi_id=boards.target.id,
    )] == [
        ("assistant", "Please review the integration."),
        ("user", "Integration is sound."),
    ]


def test_notify_publish_and_claim_write_their_respective_message_books(transcript_boards) -> None:
    boards = transcript_boards
    job_id = boards.source_notifies.publish(
        A2ANotifyJob(
            source_magi_id=boards.source.id,
            target_magi_id=boards.target.id,
            text="The deployment window is open.",
        )
    )
    assert [(m.role, m.text) for m in _peer_messages(
        boards.source_conversations,
        boards.source_messages,
        peer_magi_id=boards.target.id,
    )] == [("assistant", "The deployment window is open.")]

    claimed = boards.target_notifies.claim_for_target(
        magi_id=boards.target.id, worker_id="target-worker"
    )
    assert claimed is not None
    assert claimed.job_id == job_id
    assert [(m.role, m.text) for m in _peer_messages(
        boards.target_conversations,
        boards.target_messages,
        peer_magi_id=boards.source.id,
    )] == [("user", "The deployment window is open.")]


@pytest.mark.asyncio
async def test_target_worker_reloads_local_a2a_transcript_on_later_request(transcript_boards) -> None:
    """A second request sees the first request and its target-side reply."""
    from magi.agent.worker import AgentWorker

    boards = transcript_boards
    first_id = boards.source_requests.publish(
        A2ARequestJob(
            source_magi_id=boards.source.id,
            target_magi_id=boards.target.id,
            text="First request.",
        )
    )
    assert boards.target_requests.claim_for_target(
        magi_id=boards.target.id, worker_id="target-worker"
    ) is not None
    boards.target_requests.submit_result(
        job_id=first_id,
        worker_id="target-worker",
        result=A2ARequestResult(
            job_id=first_id,
            status=JobStatus.COMPLETED,
            content="First answer.",
        ),
    )

    boards.source_requests.publish(
        A2ARequestJob(
            source_magi_id=boards.source.id,
            target_magi_id=boards.target.id,
            text="Second request.",
        )
    )
    worker = AgentWorker(
        SimpleNamespace(
            agent_job_board=SimpleNamespace(claim_for_new_conversation=lambda **_kwargs: None),
            a2a_request_job_board=boards.target_requests,
            a2a_notify_job_board=boards.target_notifies,
            conversations_book=boards.target_conversations,
            messages_book=boards.target_messages,
            delivery_notify_job_board=Mock(),
                settings_book=SimpleNamespace(get_value=lambda **_kwargs: None),
        ),
        magi_id=boards.target.id,
    )  # type: ignore[arg-type]
    captured = []

    async def direct_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def claim_second_request():
        return (
            "a2a.request",
            boards.target_requests.claim_for_target(
                magi_id=boards.target.id, worker_id=worker.worker_id
            ),
        )

    async def capture_context(ctx):
        await worker._load_history(ctx)
        captured.append(ctx)
        ctx.final_reply = "Second answer."
        worker._stopping = True

    worker.call = direct_call  # type: ignore[method-assign]
    worker._claim_next_turn = claim_second_request  # type: ignore[method-assign]
    worker._process = capture_context  # type: ignore[method-assign]
    await worker._run()

    assert len(captured) == 1
    ctx = captured[0]
    expected_conversation = boards.target_conversations.get_or_create_for_a2a_peer(
        peer_magi_id=boards.source.id
    )
    assert ctx.conversation_id == expected_conversation.id
    assert ctx.messages == [
        {"role": "user", "content": "First request."},
        {"role": "assistant", "content": "First answer."},
        {"role": "user", "content": "Second request."},
    ]


def test_request_is_targeted_and_returns_one_durable_response(boards) -> None:
    source, target, _memberships, requests, _notifies = boards
    job_id = requests.publish(
        A2ARequestJob(
            source_magi_id=source.id,
            target_magi_id=target.id,
            text="Please validate the build plan.",
        )
    )

    assert requests.claim_for_target(magi_id=source.id, worker_id="source-worker") is None
    claimed = requests.claim_for_target(magi_id=target.id, worker_id="target-worker")
    assert claimed is not None
    assert claimed.job_id == job_id
    assert claimed.source_magi_id == source.id
    assert requests.get_result(job_id=job_id) is None

    requests.submit_result(
        job_id=job_id,
        worker_id="target-worker",
        result=A2ARequestResult(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            content="The plan builds cleanly.",
        ),
    )
    result = requests.get_result(job_id=job_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED
    assert result.content == "The plan builds cleanly."
    assert result.error_code is None

    # A terminal request can never be overwritten by another response.
    requests.submit_result(
        job_id=job_id,
        worker_id="target-worker",
        result=A2ARequestResult(job_id=job_id, status=JobStatus.COMPLETED, content="different"),
    )
    assert requests.get_result(job_id=job_id).content == "The plan builds cleanly."


def test_notify_is_reliably_consumed_but_has_no_sender_wait_contract(boards) -> None:
    source, target, _memberships, _requests, notifies = boards
    job_id = notifies.publish(
        A2ANotifyJob(
            source_magi_id=source.id,
            target_magi_id=target.id,
            text="Deployment has completed.",
        )
    )
    assert notifies.claim_for_target(magi_id=source.id, worker_id="source-worker") is None
    claimed = notifies.claim_for_target(magi_id=target.id, worker_id="target-worker")
    assert claimed is not None
    assert claimed.job_id == job_id
    notifies.submit_result(
        job_id=job_id,
        worker_id="target-worker",
        result=A2ANotifyResult(job_id=job_id, status=JobStatus.COMPLETED),
    )
    assert notifies.get_result(job_id=job_id).status == JobStatus.COMPLETED
    assert notifies.get_result(job_id=job_id).error_code is None


def test_route_is_scoped_to_one_magis_and_deadlines_remain_caller_policy(boards) -> None:
    source, target, memberships, requests, _notifies = boards
    with pytest.raises(ValueError, match="sending MAGI"):
        requests.publish(
            A2ARequestJob(
                source_magi_id=source.id,
                target_magi_id=source.id,
                text="self message",
            )
        )

    other_magis = MagisBook(requests._factory).get(MagisBook(requests._factory).add(Magis(name='Other')))
    other_role = MagisRoleBook(requests._factory).get(MagisRoleBook(requests._factory).add(MagisRole(magis_id=other_magis.id, name='EVA')))
    other_member = memberships.get(
        memberships.add(MagisMembership(magis_id=other_magis.id, role_id=other_role.id))
    )
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
            source_magi_id=source.id,
            target_magi_id=target.id,
            text="too late",
            deadline_at=utcnow_naive() - timedelta(seconds=1),
        )
    )
    claimed = requests.claim_for_target(magi_id=target.id, worker_id="target-worker")
    assert claimed is not None
    assert claimed.job_id == expired_id
    assert requests.get_result(job_id=expired_id) is None


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
        tool_catalog_book=SimpleNamespace(get_current=lambda: None),
        tool_job_board=SimpleNamespace(publish=lambda _job: "tool-job"),
    )
    worker = AgentWorker(bus, magi_id=11)  # type: ignore[arg-type]

    async def direct_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    worker.call = direct_call  # type: ignore[method-assign]
    ctx = RunContext(contact_id=None, conversation_id=0, channel="webui")
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
    worker = AgentWorker(SimpleNamespace(delivery_notify_job_board=delivery_board))  # type: ignore[arg-type]
    ctx = RunContext(
        contact_id=None,
        conversation_id=0,
        channel="a2a.request",
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
        delivery_notify_job_board=delivery_board,
        settings_book=SimpleNamespace(get_value=lambda **_kwargs: None),
        agent_job_board=Mock(),
    )
    worker = AgentWorker(bus, magi_id=12)  # type: ignore[arg-type]
    job = A2ARequestJob(
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
            source_magi_id=source.id,
            target_magi_id=target.id,
            text="Return one collaboration result.",
        )
    )
    delivery_board = Mock()
    worker = AgentWorker(
        SimpleNamespace(
            agent_job_board=SimpleNamespace(claim_for_new_conversation=lambda **_kwargs: None),
            a2a_request_job_board=requests,
            a2a_notify_job_board=notifies,
            delivery_notify_job_board=delivery_board,
            settings_book=SimpleNamespace(get_value=lambda **_kwargs: None),
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

    result = requests.get_result(job_id=request_id)
    assert result is not None
    assert result.status == JobStatus.COMPLETED
    assert result.content == "Collaboration completed."
    delivery_board.publish.assert_not_called()
