from __future__ import annotations

import ast
from pathlib import Path

import pytest

from magi.new_bus import (
    AppendMessageJob,
    ArchiveMessagesJob,
    BaseJob,
    Bus,
    CreateConversationJob,
    JobStatus,
    ListConversationMessagesJob,
    MessageRole,
)
from magi.new_bus.firmware.books.messageBook import MessageBook
from magi.new_bus.firmware.jobs.conversationJobs import CreateConversationJobBoard
from magi.new_bus.firmware.jobs.messageJobs import (
    AppendMessageJobBoard,
    ArchiveMessagesJobBoard,
    ListConversationMessagesJobBoard,
)
from tests.unit.new_bus.testing import WORKER, attach_board

BOARD_BY_JOB = {
    CreateConversationJob: CreateConversationJobBoard,
    AppendMessageJob: AppendMessageJobBoard,
    ArchiveMessagesJob: ArchiveMessagesJobBoard,
    ListConversationMessagesJob: ListConversationMessagesJobBoard,
}


@pytest.fixture
def bus(tmp_path) -> Bus:
    return Bus(tmp_path)


def _board(bus: Bus, job: BaseJob):
    return attach_board(bus, BOARD_BY_JOB[type(job)], worker_id=WORKER, slots=("publish",))


def _publish[JobT: BaseJob](bus: Bus, job: JobT) -> JobT:
    job.id = _board(bus, job).publish(job)
    return job


def _result(bus: Bus, job: BaseJob):
    return _board(bus, job).get_result(job.id)


def _conversation_id(bus: Bus) -> int:
    created = _publish(
        bus,
        CreateConversationJob(delivery_address="webui:test", channel="webui"),
    )
    outcome = _result(bus, created)
    assert outcome is not None
    assert outcome.conversation_id is not None
    return outcome.conversation_id


def test_append_and_list_messages_follow_the_conversation_contract(bus: Bus) -> None:
    conversation_id = _conversation_id(bus)
    first = _publish(
        bus,
        AppendMessageJob(conversation_id=conversation_id, role=MessageRole.USER, content="hello"),
    )
    appended = _result(bus, first)
    assert appended is not None
    assert appended.status is JobStatus.COMPLETED
    assert appended.message_id is not None
    message = MessageBook(bus._factory).get(appended.message_id)
    assert message is not None
    assert message.role is MessageRole.USER
    assert message.content == "hello"

    _publish(
        bus,
        AppendMessageJob(conversation_id=conversation_id, role=MessageRole.ASSISTANT, content="hi"),
    )
    listed = _publish(bus, ListConversationMessagesJob(conversation_id=conversation_id))
    transcript = _result(bus, listed)
    assert transcript is not None
    assert [item.content for item in transcript.messages] == ["hello", "hi"]


def test_archive_is_scoped_to_one_conversation_and_hidden_by_default(bus: Bus) -> None:
    conversation_id = _conversation_id(bus)
    first = _publish(
        bus, AppendMessageJob(conversation_id=conversation_id, role=MessageRole.USER, content="old")
    )
    first_outcome = _result(bus, first)
    assert first_outcome is not None
    assert first_outcome.message_id is not None
    _publish(
        bus, AppendMessageJob(conversation_id=conversation_id, role=MessageRole.USER, content="new")
    )

    archived = _publish(
        bus,
        ArchiveMessagesJob(
            conversation_id=conversation_id, before_message_id=first_outcome.message_id + 1
        ),
    )
    archive_result = _result(bus, archived)
    assert archive_result is not None
    assert archive_result.archived_count == 1

    live_job = _publish(bus, ListConversationMessagesJob(conversation_id=conversation_id))
    live = _result(bus, live_job)
    assert live is not None
    assert [item.content for item in live.messages] == ["new"]
    all_messages_job = _publish(
        bus, ListConversationMessagesJob(conversation_id=conversation_id, include_archived=True)
    )
    all_messages = _result(bus, all_messages_job)
    assert all_messages is not None
    assert [item.content for item in all_messages.messages] == ["old", "new"]


def test_append_returns_failure_only_when_its_foreign_key_is_missing(bus: Bus) -> None:
    missing = _publish(bus, AppendMessageJob(conversation_id=999, content="hello"))
    missing_result = _result(bus, missing)
    assert missing_result is not None
    assert missing_result.status is JobStatus.FAILED

    empty = _publish(bus, AppendMessageJob(conversation_id=_conversation_id(bus), content="  "))
    empty_result = _result(bus, empty)
    assert empty_result is not None
    assert empty_result.status is JobStatus.COMPLETED
    assert empty_result.message_id is not None
    message = MessageBook(bus._factory).get(empty_result.message_id)
    assert message is not None
    assert message.content == "  "


def test_message_book_stays_private_to_firmware() -> None:
    import magi.new_bus.firmware as firmware

    assert "MessageBook" not in firmware.__all__
    assert not hasattr(firmware, "MessageBook")
    assert "ConversationBook" not in firmware.__all__
    assert not hasattr(firmware, "ConversationBook")
    assert "AppendMessageJob" in firmware.__all__


def test_base_does_not_import_firmware() -> None:
    root = Path(__file__).resolve().parents[3] / "magi" / "new_bus" / "base"
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
