from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from magi.new_bus import (
    AppendMessageJob,
    ArchiveMessagesJob,
    Bus,
    Conversation,
    CreateConversationJob,
    InvalidJobError,
    JobStatus,
    ListConversationMessagesJob,
    MessageRole,
    SQLiteBackend,
    UpdateConversationSummaryJob,
)
from magi.new_bus.testing import WORKER, InMemoryBackend, occupy


def _bus() -> Bus:
    item = Bus(InMemoryBackend())
    occupy(item)
    return item


def _publish[JobT](bus: Bus, job: JobT) -> JobT:
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


def test_command_validation_becomes_a_terminal_job_result() -> None:
    bus = _bus()
    invalid = _publish(bus, CreateConversationJob(contact_id=1, channel="webui"))
    outcome = bus.get_result(invalid)
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "delivery_address is required"


def test_firmware_commands_are_not_claimable_work() -> None:
    bus = _bus()
    with pytest.raises(InvalidJobError, match="cannot be claimed"):
        bus.claim(CreateConversationJob, worker_id=WORKER)


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
