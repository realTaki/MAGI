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
from bus.firmware.books.taskBook import Task, TaskRow, TaskSource
from bus.firmware.books.toolsBook import LLMTool, Tool, ToolRow
from bus.firmware.jobs.contactNoteJobs import ListContactNotesJob, ListContactNotesJobBoard


@pytest.mark.parametrize(
    ("record_cls", "row_cls", "required_values", "required_columns"),
    [
        (Contact, ContactRow, {}, {"name", "role", "last_seen_at"}),
        (ContactNote, ContactNoteRow, {}, {"contact_id", "note", "kind"}),
        (Conversation, ConversationRow, {}, {"delivery_address", "channel", "topic", "summary"}),
        (Memory, MemoryRow, {}, {"topic", "detail", "kind", "archived"}),
        (
            Message,
            MessageRow,
            {"contact_id": 1, "content": "message", "conversation_id": 1, "archived": False},
            {"contact_id", "content", "conversation_id", "archived"},
        ),
        (Setting, SettingRow, {"key": "key", "value": "value"}, {"key", "value"}),
        (
            Task,
            TaskRow,
            {
                "conversation_id": 1,
                "prompt": "prompt",
                "cron": "* * * * *",
                "name": "task",
                "source": TaskSource.USER,
                "enabled": True,
            },
            {"conversation_id", "prompt", "cron", "name", "source", "enabled"},
        ),
        (
            Tool,
            ToolRow,
            {
                "name": "tool",
                "definition": LLMTool(name="tool", description="description", input_schema={}),
                "enabled": True,
            },
            {"name", "definition", "enabled"},
        ),
    ],
)
def test_book_records_allow_none_without_widening_row_constraints(
    record_cls, row_cls, required_values, required_columns
) -> None:
    domain_fields = {
        field.name: None
        for field in fields(record_cls)
        if field.name not in {"id", "created_at", "updated_at", *required_values}
    }

    record = record_cls(**(required_values | domain_fields))

    assert all(getattr(record, name) is None for name in domain_fields)
    assert all(getattr(record, name) is not None for name in required_values)
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


def test_list_contact_notes_can_omit_the_kind_filter() -> None:
    book = type("Book", (), {"list": lambda _self, **filters: [filters]})()
    result = ListContactNotesJobBoard(None, book=book)._execute(
        ListContactNotesJob(publisher="test", contact_id=7, kind=None)
    )

    assert result.contact_notes == [
        {"contact_id": 7, "kind": None},
    ]
