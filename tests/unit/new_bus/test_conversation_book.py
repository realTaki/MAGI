from __future__ import annotations

import dataclasses
from datetime import datetime

from sqlalchemy import create_engine, inspect, text

from magi.new_bus import (
    AddConversationMemberJob,
    AppendMessageJob,
    ArchiveMessagesJob,
    BaseJob,
    BaseJobResult,
    Bus,
    Conversation,
    CreateConversationJob,
    JobStatus,
    ListConversationMembersJob,
    ListConversationMessagesJob,
    MessageRole,
    RemoveConversationMemberJob,
    SQLiteBackend,
    UpdateConversationSummaryJob,
)
from magi.new_bus.firmware.books.contactBook import Contact, ContactBook
from magi.new_bus.firmware.books.conversationBook import ConversationBook
from magi.new_bus.firmware.jobs.conversationJobs import (
    CreateConversationJobBoard,
    UpdateConversationSummaryJobBoard,
)
from magi.new_bus.firmware.jobs.convMembersJobs import (
    AddConversationMemberJobBoard,
    ListConversationMembersJobBoard,
    RemoveConversationMemberJobBoard,
)
from magi.new_bus.firmware.jobs.messageJobs import (
    AppendMessageJobBoard,
    ArchiveMessagesJobBoard,
    ListConversationMessagesJobBoard,
)
from tests.unit.new_bus.testing import WORKER, InMemoryBackend, attach_board

BOARD_BY_JOB = {
    AddConversationMemberJob: AddConversationMemberJobBoard,
    CreateConversationJob: CreateConversationJobBoard,
    ListConversationMembersJob: ListConversationMembersJobBoard,
    AppendMessageJob: AppendMessageJobBoard,
    ArchiveMessagesJob: ArchiveMessagesJobBoard,
    ListConversationMessagesJob: ListConversationMessagesJobBoard,
    RemoveConversationMemberJob: RemoveConversationMemberJobBoard,
    UpdateConversationSummaryJob: UpdateConversationSummaryJobBoard,
}


def _bus() -> Bus:
    return Bus(InMemoryBackend())


def _board(
    bus: Bus, job: BaseJob, *, worker_id: str = WORKER, slots: tuple[str, ...] = ("publish",)
):
    return attach_board(bus, BOARD_BY_JOB[type(job)], worker_id=worker_id, slots=slots)


def _publish[JobT: BaseJob](bus: Bus, job: JobT) -> JobT:
    job.id = _board(bus, job).publish(job)
    return job


def _result(bus: Bus, job: BaseJob):
    return _board(bus, job).get_result(job.id)


def _contact_id(bus: Bus, name: str = "alice") -> int:
    return ContactBook(bus._factory).add(Contact(name=name))


def _conversation(bus: Bus, conversation_id: int | None):
    assert conversation_id is not None
    return ConversationBook(bus._factory).get(conversation_id)


def test_conversation_record_keeps_transport_fields() -> None:
    assert {field.name for field in dataclasses.fields(Conversation)} >= {
        "delivery_address",
        "owner_contact_id",
        "channel",
    }


def test_create_conversation_returns_its_stable_record() -> None:
    bus = _bus()
    contact_id = _contact_id(bus)
    created = _publish(
        bus,
        CreateConversationJob(
            delivery_address="tg:123", owner_contact_id=contact_id, channel="tg", title="hello"
        ),
    )
    outcome = _result(bus, created)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    conversation = _conversation(bus, outcome.conversation_id)
    assert conversation is not None
    assert conversation.delivery_address == "tg:123"
    assert conversation.owner_contact_id == contact_id
    assert conversation.channel == "tg"
    assert conversation.title == "hello"


def test_conversation_members_are_current_non_owner_contacts() -> None:
    bus = _bus()
    owner_contact_id = _contact_id(bus, "owner")
    member_contact_id = _contact_id(bus, "member")
    created = _publish(
        bus,
        CreateConversationJob(owner_contact_id=owner_contact_id, channel="tg"),
    )
    created_result = _result(bus, created)
    assert created_result is not None
    assert created_result.conversation_id is not None

    added = _publish(
        bus,
        AddConversationMemberJob(
            conversation_id=created_result.conversation_id,
            contact_id=member_contact_id,
        ),
    )
    added_result = _result(bus, added)
    assert added_result is not None
    assert added_result.status is JobStatus.COMPLETED

    duplicate_result = _result(
        bus,
        _publish(
            bus,
            AddConversationMemberJob(
                conversation_id=created_result.conversation_id,
                contact_id=member_contact_id,
            ),
        ),
    )
    assert duplicate_result is not None
    assert duplicate_result.status is JobStatus.COMPLETED

    listed_result = _result(
        bus,
        _publish(
            bus,
            ListConversationMembersJob(conversation_id=created_result.conversation_id),
        ),
    )
    assert listed_result is not None
    assert [member.contact_id for member in listed_result.members] == [member_contact_id]

    removed_result = _result(
        bus,
        _publish(
            bus,
            RemoveConversationMemberJob(
                conversation_id=created_result.conversation_id,
                contact_id=member_contact_id,
            ),
        ),
    )
    assert removed_result is not None
    assert removed_result.status is JobStatus.COMPLETED


def test_conversation_owner_migration_keeps_legacy_data(tmp_path) -> None:
    path = tmp_path / "legacy-conversations.sqlite"
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

    bus = Bus(SQLiteBackend(path))
    try:
        with bus._factory.engine.connect() as connection:
            columns = {column["name"] for column in inspect(connection).get_columns("books_conversations")}
            assert "contact_id" not in columns
            assert connection.execute(
                text("SELECT owner_contact_id FROM books_conversations")
            ).scalar_one() == 1
            assert "books_conv_members" in inspect(connection).get_table_names()
    finally:
        bus.close()


def test_update_summary_is_a_named_operation() -> None:
    bus = _bus()
    created = _publish(
        bus,
        CreateConversationJob(
            delivery_address="webui:1", owner_contact_id=_contact_id(bus), channel="webui"
        ),
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


def test_create_conversation_keeps_optional_text_unconstrained() -> None:
    bus = _bus()
    created = _publish(bus, CreateConversationJob(owner_contact_id=_contact_id(bus), channel="webui"))
    outcome = _result(bus, created)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    conversation = _conversation(bus, outcome.conversation_id)
    assert conversation is not None
    assert conversation.delivery_address == ""


def test_book_operation_persists_unexpected_failure(monkeypatch) -> None:
    bus = _bus()
    board = bus._job_board(CreateConversationJob)

    def fail(*_args):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(board, "_execute", fail)
    created = _publish(bus, CreateConversationJob(owner_contact_id=_contact_id(bus), channel="webui"))
    outcome = _result(bus, created)
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "storage unavailable"


def test_firmware_commands_are_not_claimable_work() -> None:
    bus = _bus()
    assert _board(bus, CreateConversationJob(), slots=("claim",)).claim() is None


def test_book_operation_waits_for_post_publish_approval() -> None:
    bus = _bus()
    checker = "checker"
    checker_board = _board(
        bus,
        CreateConversationJob(),
        worker_id=checker,
        slots=("post_publish", "submit_post_publish"),
    )
    created = _publish(
        bus,
        CreateConversationJob(
            delivery_address="webui:checked", owner_contact_id=_contact_id(bus), channel="webui"
        ),
    )
    assert _board(bus, created).check_job_status(created.id) is JobStatus.PREPARING
    assert _result(bus, created) is None

    pending_check = checker_board.post_publish()
    assert pending_check is not None
    assert _board(bus, created).check_job_status(created.id) is JobStatus.HOOKING
    assert checker_board.submit_post_publish(pending_check, BaseJobResult(status=JobStatus.PENDING))
    result = _result(bus, created)
    assert result is not None
    assert result.status is JobStatus.COMPLETED
    assert result.conversation_id is not None


def test_post_publish_rejection_prevents_book_operation() -> None:
    bus = _bus()
    checker = "checker"
    checker_board = _board(
        bus,
        CreateConversationJob(),
        worker_id=checker,
        slots=("post_publish", "submit_post_publish"),
    )
    created = _publish(
        bus,
        CreateConversationJob(
            delivery_address="webui:rejected", owner_contact_id=_contact_id(bus), channel="webui"
        ),
    )
    pending_check = checker_board.post_publish()
    assert pending_check is not None
    assert checker_board.submit_post_publish(
        pending_check,
        BaseJobResult(status=JobStatus.FAILED, error="channel policy rejected"),
    )
    result = _result(bus, created)
    assert result is not None
    assert result.status is JobStatus.FAILED
    assert result.error == "channel policy rejected"
    assert result.conversation_id is None


def test_post_publish_returns_false_for_an_invalid_decision() -> None:
    bus = _bus()
    checker = "checker"
    checker_board = _board(
        bus,
        CreateConversationJob(),
        worker_id=checker,
        slots=("post_publish", "submit_post_publish"),
    )
    created = _publish(
        bus,
        CreateConversationJob(
            delivery_address="webui:checked", owner_contact_id=_contact_id(bus), channel="webui"
        ),
    )
    pending_check = checker_board.post_publish()
    assert pending_check is not None
    assert not checker_board.submit_post_publish(pending_check, BaseJobResult())
    assert _board(bus, created).check_job_status(created.id) is JobStatus.HOOKING


def test_chat_commands_and_results_survive_sqlite_reopen(tmp_path) -> None:
    path = tmp_path / "firmware.sqlite"
    first = Bus(SQLiteBackend(path))
    try:
        created = _publish(
            first,
            CreateConversationJob(
                delivery_address="webui:durable",
                owner_contact_id=_contact_id(first, "durable"),
                channel="webui",
            ),
        )
        created_result = _result(first, created)
        assert created_result is not None
        assert created_result.conversation_id is not None
        appended = _publish(
            first,
            AppendMessageJob(
                conversation_id=created_result.conversation_id,
                role=MessageRole.USER,
                content="persist me",
            ),
        )
    finally:
        first.close()

    reopened = Bus(SQLiteBackend(path))
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
