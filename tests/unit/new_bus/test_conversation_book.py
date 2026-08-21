from __future__ import annotations

import dataclasses
from datetime import datetime

from magi.new_bus import (
    AppendMessageJob,
    ArchiveMessagesJob,
    BaseJob,
    BaseJobResult,
    Bus,
    Conversation,
    CreateConversationJob,
    JobStatus,
    ListConversationMessagesJob,
    MessageRole,
    Slot,
    SQLiteBackend,
    UpdateConversationSummaryJob,
)
from tests.unit.new_bus.support import WORKER, InMemoryBackend, occupy


def _bus() -> Bus:
    item = Bus(InMemoryBackend())
    occupy(item)
    return item


def _publish[JobT: BaseJob](bus: Bus, job: JobT) -> JobT:
    job.id = bus.publish(job, worker_id=WORKER)
    return job


def test_firmware_exposes_chat_contracts_not_books() -> None:
    bus = _bus()
    assert bus.books == ()
    assert {
        CreateConversationJob,
        ListConversationMessagesJob,
        ArchiveMessagesJob,
        UpdateConversationSummaryJob,
    } <= set(bus.jobs)
    assert {field.name for field in dataclasses.fields(Conversation)} >= {
        "delivery_address",
        "contact_id",
        "channel",
    }


def test_create_conversation_returns_its_stable_record() -> None:
    bus = _bus()
    created = _publish(
        bus,
        CreateConversationJob(delivery_address="tg:123", contact_id=7, channel="tg", title="hello"),
    )
    outcome = bus.get_result(created)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    assert outcome.conversation is not None
    assert outcome.conversation.delivery_address == "tg:123"
    assert outcome.conversation.contact_id == 7
    assert outcome.conversation.channel == "tg"
    assert outcome.conversation.title == "hello"


def test_update_summary_is_a_named_operation() -> None:
    bus = _bus()
    created = _publish(
        bus, CreateConversationJob(delivery_address="webui:1", contact_id=1, channel="webui")
    )
    created_outcome = bus.get_result(created)
    assert created_outcome is not None
    conversation = created_outcome.conversation
    assert conversation is not None

    updated = _publish(
        bus,
        UpdateConversationSummaryJob(conversation_id=conversation.id, summary="compact context"),
    )
    outcome = bus.get_result(updated)
    assert outcome is not None
    assert outcome.conversation is not None
    assert outcome.conversation.summary == "compact context"
    assert isinstance(outcome.conversation.last_compaction_at, datetime)


def test_create_conversation_keeps_optional_text_unconstrained() -> None:
    bus = _bus()
    created = _publish(bus, CreateConversationJob(contact_id=1, channel="webui"))
    outcome = bus.get_result(created)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    assert outcome.conversation is not None
    assert outcome.conversation.delivery_address == ""


def test_book_operation_persists_unexpected_failure(monkeypatch) -> None:
    bus = _bus()
    board = bus.job_board(CreateConversationJob)

    def fail(*_args):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(board, "_execute", fail)
    created = _publish(bus, CreateConversationJob(contact_id=1, channel="webui"))
    outcome = bus.get_result(created)
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "storage unavailable"


def test_firmware_commands_are_not_claimable_work() -> None:
    bus = _bus()
    assert bus.claim(CreateConversationJob, worker_id=WORKER) is None


def test_book_operation_waits_for_post_publish_approval() -> None:
    bus = _bus()
    checker = "checker"
    bus.attach(
        checker,
        (
            Slot(CreateConversationJob, "post_publish"),
            Slot(CreateConversationJob, "submit_post_publish"),
        ),
    )
    created = _publish(
        bus, CreateConversationJob(delivery_address="webui:checked", contact_id=1, channel="webui")
    )
    assert bus.check_job_status(created) is JobStatus.PREPARING
    assert bus.get_result(created) is None

    pending_check = bus.post_publish(CreateConversationJob, worker_id=checker)
    assert pending_check is not None
    assert bus.check_job_status(created) is JobStatus.HOOKING
    assert bus.submit_post_publish(
        pending_check, BaseJobResult(status=JobStatus.PENDING), worker_id=checker
    )
    result = bus.get_result(created)
    assert result is not None
    assert result.status is JobStatus.COMPLETED
    assert result.conversation is not None


def test_post_publish_rejection_prevents_book_operation() -> None:
    bus = _bus()
    checker = "checker"
    bus.attach(
        checker,
        (
            Slot(CreateConversationJob, "post_publish"),
            Slot(CreateConversationJob, "submit_post_publish"),
        ),
    )
    created = _publish(
        bus, CreateConversationJob(delivery_address="webui:rejected", contact_id=1, channel="webui")
    )
    pending_check = bus.post_publish(CreateConversationJob, worker_id=checker)
    assert pending_check is not None
    assert bus.submit_post_publish(
        pending_check,
        BaseJobResult(status=JobStatus.FAILED, error="channel policy rejected"),
        worker_id=checker,
    )
    result = bus.get_result(created)
    assert result is not None
    assert result.status is JobStatus.FAILED
    assert result.error == "channel policy rejected"
    assert result.conversation is None


def test_post_publish_returns_false_for_an_invalid_decision() -> None:
    bus = _bus()
    checker = "checker"
    bus.attach(
        checker,
        (
            Slot(CreateConversationJob, "post_publish"),
            Slot(CreateConversationJob, "submit_post_publish"),
        ),
    )
    created = _publish(
        bus, CreateConversationJob(delivery_address="webui:checked", contact_id=1, channel="webui")
    )
    pending_check = bus.post_publish(CreateConversationJob, worker_id=checker)
    assert pending_check is not None
    assert not bus.submit_post_publish(pending_check, BaseJobResult(), worker_id=checker)
    assert bus.check_job_status(created) is JobStatus.HOOKING


def test_chat_commands_and_results_survive_sqlite_reopen(tmp_path) -> None:
    path = tmp_path / "firmware.sqlite"
    first = Bus(SQLiteBackend(path))
    try:
        occupy(first)
        created = _publish(
            first,
            CreateConversationJob(delivery_address="webui:durable", contact_id=3, channel="webui"),
        )
        created_result = first.get_result(created)
        assert created_result is not None
        assert created_result.conversation is not None
        appended = _publish(
            first,
            AppendMessageJob(
                conversation_id=created_result.conversation.id,
                role=MessageRole.USER,
                content="persist me",
            ),
        )
    finally:
        first.close()

    reopened = Bus(SQLiteBackend(path))
    try:
        occupy(reopened)
        append_result = reopened.get_result(appended)
        assert append_result is not None
        assert append_result.message is not None
        listed = _publish(
            reopened,
            ListConversationMessagesJob(conversation_id=append_result.message.conversation_id),
        )
        transcript = reopened.get_result(listed)
        assert transcript is not None
        assert [message.content for message in transcript.messages] == ["persist me"]
    finally:
        reopened.close()
