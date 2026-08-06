"""Unit tests for new_bus Books.

Each test exercises basic CRUD on one Book.  All tests use an
in-memory SQLite via :func:`EngineFactory` to keep them isolated.
"""

from __future__ import annotations

import pytest

from magi.new_bus.db import EngineFactory
from magi.new_bus.books.local import (
    ActionItem,
    ActionItemBook,
    Contact,
    ContactBook,
    ContactNoteBook,
    HookSignoffBook,
    McpServer,
    McpServerBook,
    Memory,
    MemoryBook,
    Message,
    MessageBook,
    Setting,
    SettingBook,
    Session,
    SessionBook,
    Task,
    TaskBook,
    TaskPreset,
    TaskPresetBook,
    TaskRun,
    TaskRunBook,
    TokenUsageBook,
    ToolCatalogState,
    ToolCatalogStateBook,
    ToolDefinition,
    ToolDefinitionBook,
)


@pytest.fixture
def factory():
    """Fresh in-memory SQLite per test."""
    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    return f


@pytest.fixture
def contact_id(factory):
    """Create a contact row, return its id.  Tests that need a contact
    FK can use this fixture to get a valid uid."""
    from magi.new_bus.books.local.contactBook import ContactBook
    c = ContactBook(factory).add(name="Fixture")
    return c.id


# -- SettingBook --------------------------------------------------------


def test_setting_book_get_set(factory):
    book = SettingBook(factory)
    assert book.get(key="system.timezone") is None
    s = book.set(key="system.timezone", value="UTC")
    assert isinstance(s, Setting)
    assert s.key == "system.timezone"
    assert s.value == "UTC"
    assert book.get(key="system.timezone") == "UTC"


def test_setting_book_list_and_delete(factory):
    book = SettingBook(factory)
    book.set(key="a", value="1")
    book.set(key="b", value="2")
    assert set(book.list_keys()) == {"a", "b"}
    assert book.delete(key="a") is True
    assert book.delete(key="nonexistent") is False
    assert book.list_keys() == ["b"]


# -- MemoryBook ---------------------------------------------------------


def test_memory_book_add_and_get(factory, contact_id):
    book = MemoryBook(factory)
    m = book.add(uid=contact_id, kind="important", subject="alice", body="likes cats")
    assert isinstance(m, Memory)
    assert m.uid == contact_id
    assert m.body == "likes cats"
    found = book.get(memory_id=m.id)
    assert found is not None and found.body == "likes cats"


def test_memory_book_list_by_owner(factory, contact_id):
    from magi.new_bus.books.local.contactBook import ContactBook
    book = MemoryBook(factory)
    cbook = ContactBook(factory)
    other_id = cbook.add(name="Other").id
    book.add(uid=contact_id, kind="important", subject="a", body="x")
    book.add(uid=other_id, kind="important", subject="b", body="y")
    assert len(book.list_by_owner(uid=contact_id)) == 1
    assert len(book.list_by_owner(uid=other_id)) == 1


# -- ContactBook + ContactNoteBook --------------------------------------


def test_contact_book_full_lifecycle(factory):
    book = ContactBook(factory)
    c = book.add(name="Alice", telegram_id=12345, admin=True)
    assert isinstance(c, Contact)
    assert c.name == "Alice" and c.admin is True

    found = book.get(contact_id=c.id)
    assert found is not None and found.telegram_id == 12345

    tg = book.get_by_telegram(telegram_id=12345)
    assert tg is not None and tg.id == c.id

    book.set_admin(contact_id=c.id, admin=False)
    assert book.get(contact_id=c.id).admin is False


def test_contact_note_book(factory):
    cbook = ContactBook(factory)
    nbook = ContactNoteBook(factory)
    c = cbook.add(name="Bob")
    n = nbook.add(contact_id=c.id, note="works in finance")
    assert n.contact_id == c.id
    assert len(nbook.list_for_contact(contact_id=c.id)) == 1


# -- SessionBook + MessageBook -----------------------------------------


def test_session_and_message(factory):
    sbook = SessionBook(factory)
    mbook = MessageBook(factory)

    s = sbook.add(
        session_id="01ABC",
        delivery_address="tg:12345",
        uid=1,
        channel="tg",
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    assert isinstance(s, Session)
    assert s.session_id == "01ABC"

    m = mbook.add(
        session_id="01ABC",
        message_id="m1",
        role="user",
        text="hi",
        ts="2026-08-05T00:00:01Z",
    )
    assert isinstance(m, Message)
    msgs = mbook.list_for_session(session_id="01ABC")
    assert len(msgs) == 1
    assert msgs[0].text == "hi"


# -- McpServerBook ------------------------------------------------------


def test_mcp_server_book(factory):
    book = McpServerBook(factory)
    s = book.add(name="gmail", transport="stdio", config={"cmd": "mcp-gmail"})
    assert isinstance(s, McpServer)
    assert s.name == "gmail"
    assert book.list_enabled()[0].name == "gmail"


# -- ActionItemBook -----------------------------------------------------


def test_action_item_book(factory, contact_id):
    book = ActionItemBook(factory)
    item = book.add(uid=contact_id, kind="alert", title="x", body="y")
    assert isinstance(item, ActionItem)
    assert item.status == "open"
    book.mark_done(item_id=item.id)
    assert book.get(item_id=item.id).status == "done"


# -- TokenUsageBook ----------------------------------------------------


def test_token_usage_book(factory, contact_id):
    book = TokenUsageBook(factory)
    book.add(uid=contact_id, provider="openai", model="gpt-4",
             input_tokens=10, output_tokens=20)
    book.add(uid=contact_id, provider="openai", model="gpt-4",
             input_tokens=5, output_tokens=10)
    in_total, out_total = book.sum_for_run(run_id="r1")  # 0 rows for r1
    assert in_total == 0
    book.add(uid=contact_id, provider="openai", model="gpt-4", run_id="r1",
             input_tokens=100, output_tokens=200)
    in_total, out_total = book.sum_for_run(run_id="r1")
    assert in_total == 100
    assert out_total == 200


# -- ToolCatalogStateBook + ToolDefinitionBook -------------------------


def test_tool_catalog_bump(factory):
    sbook = ToolCatalogStateBook(factory)
    assert sbook.get() is None
    s = sbook.bump(revision=1, snapshot_hash="abc")
    assert isinstance(s, ToolCatalogState)
    assert s.revision == 1
    s2 = sbook.bump(revision=2, snapshot_hash="def")
    assert s2.revision == 2


def test_tool_definition_upsert(factory):
    book = ToolDefinitionBook(factory)
    t = book.upsert(name="echo", spec_json='{"x":1}', description="echoes")
    assert isinstance(t, ToolDefinition)
    assert t.name == "echo"
    # upsert again
    t2 = book.upsert(name="echo", spec_json='{"x":2}', description="echoes v2")
    assert t2.id == t.id
    assert t2.spec_json == '{"x":2}'


# -- TaskBook + TaskRunBook + TaskPresetBook -------------------------


def test_task_book_lifecycle(factory, contact_id):
    pbook = TaskPresetBook(factory)
    preset = pbook.add(
        id="p1", key="daily", name="Daily", prompt="hi",
        frequency="daily", hour=9, minute=0, target_channel="webui",
    )
    assert isinstance(preset, TaskPreset)

    tbook = TaskBook(factory)
    t = tbook.add(
        id="t1", name="MyTask", prompt="do", cron="0 9 * * *",
        uid=contact_id, target_channel="webui",
        preset_id="p1", preset_key="daily",
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    assert isinstance(t, Task)
    assert t.preset_key == "daily"

    rbook = TaskRunBook(factory)
    r = rbook.add(
        id="r1", task_id="t1", trigger="manual",
        started_at="2026-08-05T09:00:00Z", status="running",
    )
    assert isinstance(r, TaskRun)
    rbook.complete(run_id="r1", status="success", finished_at="2026-08-05T09:01:00Z")
    assert rbook.get(run_id="r1").status == "success"


# -- HookSignoffBook --------------------------------------------------


def test_hook_signoff_book_empty(factory):
    book = HookSignoffBook(factory)
    assert book.list_pending() == []


__all__ = [
    "ActionItem",
    "ActionItemBook",
    "Contact",
    "ContactBook",
    "ContactNoteBook",
    "HookSignoffBook",
    "McpServer",
    "McpServerBook",
    "Memory",
    "MemoryBook",
    "Message",
    "MessageBook",
    "Setting",
    "SettingBook",
    "Session",
    "SessionBook",
    "Task",
    "TaskBook",
    "TaskPreset",
    "TaskPresetBook",
    "TaskRun",
    "TaskRunBook",
    "TokenUsageBook",
    "ToolCatalogState",
    "ToolCatalogStateBook",
    "ToolDefinition",
    "ToolDefinitionBook",
]
