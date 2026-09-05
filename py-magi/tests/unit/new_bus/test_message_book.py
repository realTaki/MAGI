from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bus import (
    SYSTEM_CONTACT_ID,
    ArchiveMessagesJob,
    BaseJob,
    Bus,
    ChatNotify,
    JobStatus,
    ListConversationMessagesJob,
)
from bus.firmware.books.contactBook import Contact, ContactBook
from bus.firmware.books.conversationBook import ConversationBook
from bus.firmware.books.messageBook import MessageBook
from bus.firmware.jobs.messageJobs import (
    ArchiveMessagesJobBoard,
    ListConversationMessagesJobBoard,
)
from tests.unit.new_bus.testing import attach_board

BOARD_BY_JOB = {
    ArchiveMessagesJob: ArchiveMessagesJobBoard,
    ListConversationMessagesJob: ListConversationMessagesJobBoard,
}


@pytest.fixture
def bus(tmp_path) -> Bus:
    return Bus("@messages", workspace=tmp_path)


def _board(bus: Bus, job: BaseJob):
    return attach_board(bus, BOARD_BY_JOB[type(job)])


def _publish[JobT: BaseJob](bus: Bus, job: JobT):
    return _board(bus, job).publish(job)


def _result(bus: Bus, result):
    del bus
    return result


def _contact_id(bus: Bus, name: str = "alice") -> int:
    return ContactBook(bus._factory).add(Contact(name=name))


def _conversation_id(bus: Bus) -> int:
    return ConversationBook(bus._memories).add_for_channel(
        channel="webui",
        delivery_address="webui:test",
    )


def _say(bus: Bus, text: str, *, contact_id: int = SYSTEM_CONTACT_ID) -> None:
    chat = bus.board(ChatNotify)
    assert chat is not None
    chat.publish(
        ChatNotify(
            publisher="test",
            channel="webui",
            delivery_address="webui:test",
            contact_id=contact_id,
            text=text,
        )
    )


def test_append_and_list_messages_follow_the_conversation_contract(bus: Bus) -> None:
    conversation_id = _conversation_id(bus)
    speaker_id = _contact_id(bus)
    _say(bus, "hello", contact_id=speaker_id)
    messages = MessageBook(bus._factory).list(conversation_id=conversation_id)
    assert messages[-1].contact_id == speaker_id
    assert messages[-1].content == "hello"

    _say(bus, "hi", contact_id=_contact_id(bus, "magi"))
    listed = _publish(
        bus, ListConversationMessagesJob(publisher="test", conversation_id=conversation_id)
    )
    transcript = _result(bus, listed)
    assert transcript is not None
    assert [item.content for item in transcript.messages] == ["hello", "hi"]
    latest = _result(
        bus,
        _publish(
            bus,
            ListConversationMessagesJob(
                publisher="test", conversation_id=conversation_id, last_n=1
            ),
        ),
    )
    assert latest is not None
    assert [item.content for item in latest.messages] == ["hi"]


def test_archive_is_scoped_to_one_conversation_and_hidden_by_default(bus: Bus) -> None:
    conversation_id = _conversation_id(bus)
    speaker_id = _contact_id(bus)
    _say(bus, "old", contact_id=speaker_id)
    first = MessageBook(bus._factory).list(conversation_id=conversation_id)[-1]
    _say(bus, "new", contact_id=speaker_id)

    archived = _publish(
        bus,
        ArchiveMessagesJob(
            publisher="test",
            conversation_id=conversation_id,
            before_message_id=first.id + 1,
        ),
    )
    archive_result = _result(bus, archived)
    assert archive_result is not None
    assert archive_result.archived_count == 1

    live_job = _publish(
        bus, ListConversationMessagesJob(publisher="test", conversation_id=conversation_id)
    )
    live = _result(bus, live_job)
    assert live is not None
    assert [item.content for item in live.messages] == ["new"]
    all_messages_job = _publish(
        bus,
        ListConversationMessagesJob(
            publisher="test", conversation_id=conversation_id, include_archived=True
        ),
    )
    all_messages = _result(bus, all_messages_job)
    assert all_messages is not None
    assert [item.content for item in all_messages.messages] == ["old", "new"]


def test_message_book_stays_private_to_firmware() -> None:
    import bus.firmware as firmware

    assert "MessageBook" not in firmware.__all__
    assert not hasattr(firmware, "MessageBook")
    assert "ConversationBook" not in firmware.__all__
    assert not hasattr(firmware, "ConversationBook")
    assert "AppendMessageJob" not in firmware.__all__
    assert "ChatNotify" in firmware.__all__


def test_base_does_not_import_firmware() -> None:
    root = Path(__file__).resolve().parents[3] / "bus" / "base"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any("firmware" in name.split(".") for name in names):
                offenders.append(str(path))
    assert not offenders
