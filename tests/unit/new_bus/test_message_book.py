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
from tests.unit.new_bus.testing import WORKER, InMemoryBackend, occupy


@pytest.fixture
def bus() -> Bus:
    item = Bus(InMemoryBackend())
    occupy(item)
    return item


def _publish[JobT: BaseJob](bus: Bus, job: JobT) -> JobT:
    job.id = bus.publish(job, worker_id=WORKER)
    return job


def _conversation_id(bus: Bus) -> int:
    created = _publish(
        bus, CreateConversationJob(delivery_address="webui:test", contact_id=1, channel="webui")
    )
    outcome = bus.get_result(created)
    assert outcome is not None
    assert outcome.conversation is not None
    return outcome.conversation.id


def test_append_and_list_messages_follow_the_conversation_contract(bus: Bus) -> None:
    conversation_id = _conversation_id(bus)
    first = _publish(
        bus,
        AppendMessageJob(conversation_id=conversation_id, role=MessageRole.USER, content="hello"),
    )
    appended = bus.get_result(first)
    assert appended is not None
    assert appended.status is JobStatus.COMPLETED
    assert appended.message is not None
    assert appended.message.role is MessageRole.USER
    assert appended.message.content == "hello"

    _publish(
        bus,
        AppendMessageJob(conversation_id=conversation_id, role=MessageRole.ASSISTANT, content="hi"),
    )
    listed = _publish(bus, ListConversationMessagesJob(conversation_id=conversation_id))
    transcript = bus.get_result(listed)
    assert transcript is not None
    assert [item.content for item in transcript.messages] == ["hello", "hi"]


def test_archive_is_scoped_to_one_conversation_and_hidden_by_default(bus: Bus) -> None:
    conversation_id = _conversation_id(bus)
    first = _publish(
        bus, AppendMessageJob(conversation_id=conversation_id, role=MessageRole.USER, content="old")
    )
    first_outcome = bus.get_result(first)
    assert first_outcome is not None
    first_message = first_outcome.message
    assert first_message is not None
    _publish(
        bus, AppendMessageJob(conversation_id=conversation_id, role=MessageRole.USER, content="new")
    )

    archived = _publish(
        bus,
        ArchiveMessagesJob(conversation_id=conversation_id, before_message_id=first_message.id + 1),
    )
    archive_result = bus.get_result(archived)
    assert archive_result is not None
    assert archive_result.archived_count == 1

    live = bus.get_result(
        _publish(bus, ListConversationMessagesJob(conversation_id=conversation_id))
    )
    assert live is not None
    assert [item.content for item in live.messages] == ["new"]
    all_messages = bus.get_result(
        _publish(
            bus, ListConversationMessagesJob(conversation_id=conversation_id, include_archived=True)
        )
    )
    assert all_messages is not None
    assert [item.content for item in all_messages.messages] == ["old", "new"]


def test_append_returns_failure_only_when_its_foreign_key_is_missing(bus: Bus) -> None:
    missing = _publish(bus, AppendMessageJob(conversation_id=999, content="hello"))
    missing_result = bus.get_result(missing)
    assert missing_result is not None
    assert missing_result.status is JobStatus.FAILED

    empty = _publish(bus, AppendMessageJob(conversation_id=_conversation_id(bus), content="  "))
    empty_result = bus.get_result(empty)
    assert empty_result is not None
    assert empty_result.status is JobStatus.COMPLETED
    assert empty_result.message is not None
    assert empty_result.message.content == "  "


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
