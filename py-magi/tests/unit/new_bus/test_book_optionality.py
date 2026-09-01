from __future__ import annotations

from dataclasses import fields

import pytest

from bus import Bus
from bus.firmware.books.contactBook import Contact, ContactBook, ContactRow
from bus.firmware.books.contactNoteBook import ContactNote, ContactNoteRow
from bus.firmware.books.conversationBook import Conversation, ConversationRow
from bus.firmware.books.memoryBook import Memory, MemoryRow
from bus.firmware.books.messageBook import Message, MessageRow
from bus.firmware.books.settingsBook import Setting, SettingRow
from bus.firmware.books.taskBook import Task, TaskRow
from bus.firmware.books.toolsBook import Tool, ToolRow


@pytest.mark.parametrize(
    ("record_cls", "row_cls", "required_columns"),
    [
        (Contact, ContactRow, {"name", "role", "last_seen_at"}),
        (ContactNote, ContactNoteRow, {"contact_id", "note", "kind"}),
        (Conversation, ConversationRow, {"delivery_address", "channel", "topic", "summary"}),
        (Memory, MemoryRow, {"topic", "detail", "kind", "archived"}),
        (Message, MessageRow, {"contact_id", "content", "conversation_id", "archived"}),
        (Setting, SettingRow, {"key", "value"}),
        (Task, TaskRow, {"conversation_id", "prompt", "cron", "name", "source", "enabled"}),
        (Tool, ToolRow, {"name", "definition", "enabled"}),
    ],
)
def test_book_records_allow_none_without_widening_row_constraints(
    record_cls, row_cls, required_columns
) -> None:
    domain_fields = {
        field.name: None
        for field in fields(record_cls)
        if field.name not in {"id", "created_at", "updated_at"}
    }

    record = record_cls(**domain_fields)

    assert all(getattr(record, name) is None for name in domain_fields)
    assert all(row_cls.__table__.c[name].nullable is False for name in required_columns)


def test_book_write_omits_none_and_uses_row_defaults(tmp_path) -> None:
    with Bus("@book-optionality", workspace=tmp_path) as bus:
        book = ContactBook(bus._factory)
        contact_id = book.add(
            Contact(name="partial", role=None, last_seen_at=None)
        )
        contact = book.get(contact_id)
        assert contact is not None
        assert contact.role is not None
        assert contact.last_seen_at is not None
        assert book.update(
            Contact(id=contact_id, name="partial-renamed")
        )
        updated = book.get(contact_id)

    assert updated is not None
    assert updated.name == "partial-renamed"
    assert updated.role is not None
    assert updated.last_seen_at is not None
