from __future__ import annotations

import dataclasses
import time
from datetime import datetime

from sqlalchemy import create_engine, inspect, text

from bus import (
    Bus,
    ChatNotify,
    GetConversationJob,
    JobStatus,
    ListConversationMessagesJob,
    UpdateConversationSummaryJob,
)
from bus.firmware.books.conversationBook import Conversation, ConversationBook


def _bus(workspace) -> Bus:
    return Bus("@conversation", workspace=workspace)


def _open_conversation(
    bus: Bus,
    *,
    channel: str = "webui",
    delivery_address: str = "webui:1",
    text: str = "hi",
) -> int:
    chat = bus.board(ChatNotify)
    assert chat is not None
    chat.publish(
        ChatNotify(
            publisher="test",
            channel=channel,
            delivery_address=delivery_address,
            text=text,
        )
    )
    conversation_id = ConversationBook(bus._memories).get_or_add(
        channel=channel,
        delivery_address=delivery_address,
    )
    conversation = ConversationBook(bus._factory).get(conversation_id)
    assert conversation is not None
    return conversation.id


def test_conversation_record_keeps_transport_fields() -> None:
    assert {field.name for field in dataclasses.fields(Conversation)} >= {
        "delivery_address",
        "channel",
        "instruction",
        "info",
    }


def test_chat_notify_creates_the_conversation_for_its_endpoint(tmp_path) -> None:
    bus = _bus(tmp_path)
    conversation_id = _open_conversation(
        bus,
        channel="tg",
        delivery_address="tg:123",
        text="hello",
    )
    conversation = ConversationBook(bus._factory).get(conversation_id)
    assert conversation is not None
    assert conversation.delivery_address == "tg:123"
    assert conversation.channel == "tg"


def test_chat_notify_reuses_the_conversation_for_the_same_endpoint(tmp_path) -> None:
    bus = _bus(tmp_path)
    chat = bus.board(ChatNotify)
    assert chat is not None
    first_id = chat.publish(
        ChatNotify(
            publisher="test",
            channel="webui",
            delivery_address="webui:same",
            text="one",
        )
    )
    second_id = chat.publish(
        ChatNotify(
            publisher="test",
            channel="webui",
            delivery_address="webui:same",
            text="two",
        )
    )
    first = None
    second = None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and (first is None or second is None):
        claimed = chat.claim()
        if claimed is None:
            time.sleep(0.02)
            continue
        if first is None:
            first = claimed
        else:
            second = claimed
    assert first is not None and second is not None
    assert first is not None and second is not None
    assert {first.id, second.id} == {first_id, second_id}
    assert first.conversation_id == second.conversation_id
    listed = bus.board(ListConversationMessagesJob).publish(
        ListConversationMessagesJob(
            publisher="test",
            conversation_id=first.conversation_id,
        )
    )
    assert [message.content for message in listed.messages] == ["one", "two"]


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

    with Bus("@legacy", workspace=workspace) as bus:
        with bus._factory.engine.connect() as connection:
            columns = {column["name"] for column in inspect(connection).get_columns("books_conversations")}
            assert "contact_id" not in columns
            assert "owner_contact_id" not in columns
            assert "books_conv_members" not in inspect(connection).get_table_names()


def test_update_summary_is_a_named_operation(tmp_path) -> None:
    bus = _bus(tmp_path)
    conversation_id = _open_conversation(bus)
    updated = bus.board(UpdateConversationSummaryJob).publish(
        UpdateConversationSummaryJob(
            publisher="test",
            conversation_id=conversation_id,
            summary="compact context",
        )
    )
    assert updated.status is JobStatus.COMPLETED
    conversation = ConversationBook(bus._factory).get(conversation_id)
    assert conversation is not None
    assert conversation.summary == "compact context"
    assert isinstance(conversation.last_compaction_at, datetime)


def test_firmware_commands_are_not_claimable_work(tmp_path) -> None:
    bus = _bus(tmp_path)
    board = bus.board(GetConversationJob)
    assert board is not None
    assert not hasattr(board, "claim")


def test_chat_commands_and_results_survive_sqlite_reopen(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@durable", workspace=workspace) as first:
        conversation_id = _open_conversation(
            first,
            delivery_address="webui:durable",
            text="persist me",
        )

    with Bus("@durable", workspace=workspace) as reopened:
        listed = reopened.board(ListConversationMessagesJob).publish(
            ListConversationMessagesJob(
                publisher="test",
                conversation_id=conversation_id,
            )
        )
        assert [message.content for message in listed.messages] == ["persist me"]
