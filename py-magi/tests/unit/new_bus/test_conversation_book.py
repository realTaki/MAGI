from __future__ import annotations

import dataclasses
from datetime import datetime

from sqlalchemy import create_engine, inspect, text

from bus import (
    AppendMessageJob,
    ArchiveMessagesJob,
    BaseJob,
    Bus,
    Conversation,
    CreateConversationJob,
    JobStatus,
    ListConversationMessagesJob,
    UpdateConversationSummaryJob,
)
from bus.firmware.books.contactBook import Contact, ContactBook
from bus.firmware.books.conversationBook import ConversationBook
from bus.firmware.jobs.conversationJobs import (
    CreateConversationJobBoard,
    UpdateConversationSummaryJobBoard,
)
from bus.firmware.jobs.messageJobs import (
    AppendMessageJobBoard,
    ArchiveMessagesJobBoard,
    ListConversationMessagesJobBoard,
)
from tests.unit.new_bus.testing import attach_board, wait_result

BOARD_BY_JOB = {
    CreateConversationJob: CreateConversationJobBoard,
    AppendMessageJob: AppendMessageJobBoard,
    ArchiveMessagesJob: ArchiveMessagesJobBoard,
    ListConversationMessagesJob: ListConversationMessagesJobBoard,
    UpdateConversationSummaryJob: UpdateConversationSummaryJobBoard,
}


def _bus(workspace) -> Bus:
    return Bus(workspace)


def _board(bus: Bus, job: BaseJob):
    return attach_board(bus, BOARD_BY_JOB[type(job)])


def _publish[JobT: BaseJob](bus: Bus, job: JobT) -> JobT:
    job.id = _board(bus, job).publish(job)
    return job


def _result(bus: Bus, job: BaseJob):
    return wait_result(_board(bus, job), job.id)


def _conversation(bus: Bus, conversation_id: int | None):
    assert conversation_id is not None
    return ConversationBook(bus._factory).get(conversation_id)


def test_conversation_record_keeps_transport_fields() -> None:
    assert {field.name for field in dataclasses.fields(Conversation)} >= {
        "delivery_address",
        "channel",
        "instruction",
        "info",
    }


def test_create_conversation_returns_its_stable_record(tmp_path) -> None:
    bus = _bus(tmp_path)
    created = _publish(
        bus,
        CreateConversationJob(
            delivery_address="tg:123",
            channel="tg",
            topic="hello",
            instruction="a chat",
            info="from telegram",
        ),
    )
    outcome = _result(bus, created)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    conversation = _conversation(bus, outcome.conversation_id)
    assert conversation is not None
    assert conversation.delivery_address == "tg:123"
    assert conversation.channel == "tg"
    assert conversation.topic == "hello"
    assert conversation.instruction == "a chat"
    assert conversation.info == "from telegram"


def test_conversation_owner_migration_drops_legacy_owner_and_members(tmp_path) -> None:
    workspace = tmp_path / "legacy-workspace"
    path = workspace / "memories" / "magi.db"
    path.parent.mkdir(parents=True)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0.0.13')"))
        connection.execute(
            text(
                "CREATE TABLE books_contacts ("
                "id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                "name TEXT NOT NULL, display_name TEXT, role TEXT NOT NULL, last_seen_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE books_conversations ("
                "id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                "delivery_address TEXT NOT NULL, contact_id INTEGER NOT NULL, channel TEXT NOT NULL, "
                "title TEXT NOT NULL, summary TEXT NOT NULL, last_compaction_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE jobs_create_conversation ("
                "id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                "status TEXT NOT NULL, error TEXT, delivery_address TEXT NOT NULL, contact_id INTEGER NOT NULL, "
                "channel TEXT NOT NULL, title TEXT NOT NULL, conversation_id INTEGER)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO books_contacts VALUES "
                "(1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'owner', NULL, 'guest', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO books_conversations VALUES "
                "(1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'tg:legacy', 1, 'tg', '', '', NULL)"
            )
        )

    bus = Bus(workspace)
    try:
        with bus._factory.engine.connect() as connection:
            columns = {column["name"] for column in inspect(connection).get_columns("books_conversations")}
            assert "contact_id" not in columns
            assert "owner_contact_id" not in columns
            assert "books_conv_members" not in inspect(connection).get_table_names()
    finally:
        bus.close()


def test_update_summary_is_a_named_operation(tmp_path) -> None:
    bus = _bus(tmp_path)
    created = _publish(
        bus,
        CreateConversationJob(delivery_address="webui:1", channel="webui"),
    )
    created_outcome = _result(bus, created)
    assert created_outcome is not None
    conversation = _conversation(bus, created_outcome.conversation_id)
    assert conversation is not None

    updated = _publish(
        bus,
        UpdateConversationSummaryJob(conversation_id=conversation.id, summary="compact context"),
    )
    outcome = _result(bus, updated)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    conversation = _conversation(bus, conversation.id)
    assert conversation is not None
    assert conversation.summary == "compact context"
    assert isinstance(conversation.last_compaction_at, datetime)


def test_create_conversation_keeps_optional_text_unconstrained(tmp_path) -> None:
    bus = _bus(tmp_path)
    created = _publish(bus, CreateConversationJob(channel="webui"))
    outcome = _result(bus, created)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    conversation = _conversation(bus, outcome.conversation_id)
    assert conversation is not None
    assert conversation.delivery_address == ""


def test_book_operation_persists_unexpected_failure(tmp_path, monkeypatch) -> None:
    bus = _bus(tmp_path)
    board = bus.board(CreateConversationJob)
    assert board is not None

    def fail(*_args):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(board, "_execute", fail)
    created = _publish(bus, CreateConversationJob(channel="webui"))
    outcome = _result(bus, created)
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "storage unavailable"


def test_firmware_commands_are_not_claimable_work(tmp_path) -> None:
    bus = _bus(tmp_path)
    assert _board(bus, CreateConversationJob()).claim() is None


def test_chat_commands_and_results_survive_sqlite_reopen(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    first = Bus(workspace)
    try:
        created = _publish(
            first,
            CreateConversationJob(delivery_address="webui:durable", channel="webui"),
        )
        created_result = _result(first, created)
        assert created_result is not None
        assert created_result.conversation_id is not None
        appended = _publish(
            first,
            AppendMessageJob(
                conversation_id=created_result.conversation_id,
                contact_id=ContactBook(first._factory).add(Contact(name="durable")),
                content="persist me",
            ),
        )
    finally:
        first.close()

    reopened = Bus(workspace)
    try:
        append_result = _result(reopened, appended)
        assert append_result is not None
        assert append_result.message_id is not None
        listed = _publish(
            reopened,
            ListConversationMessagesJob(conversation_id=created_result.conversation_id),
        )
        transcript = _result(reopened, listed)
        assert transcript is not None
        assert [message.content for message in transcript.messages] == ["persist me"]
    finally:
        reopened.close()
