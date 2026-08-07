"""Unit tests for new_bus Books.

Each test exercises basic CRUD on one Book.  All tests use an
in-memory SQLite via :func:`EngineFactory` to keep them isolated.
"""

from __future__ import annotations

import pytest

from magi.new_bus.db import EngineFactory
from magi.new_bus.library.local import (
    ActionItem,
    ActionItemBook,
    ALL_SOURCES,
    Channel,
    ChannelEnum,
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
    SOURCE_PROACTIVE,
    SOURCE_USER,
    Task,
    TaskBook,
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
    from magi.new_bus.library.local.contactBook import ContactBook
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
    from magi.new_bus.library.local.contactBook import ContactBook
    book = MemoryBook(factory)
    cbook = ContactBook(factory)
    other_id = cbook.add(name="Other").id
    book.add(uid=contact_id, kind="important", subject="a", body="x")
    book.add(uid=other_id, kind="important", subject="b", body="y")
    assert len(book.list_by_owner(uid=contact_id)) == 1
    assert len(book.list_by_owner(uid=other_id)) == 1


# -- ContactBook + ContactNoteBook --------------------------------------


def test_contact_book_full_lifecycle(factory):
    """Add → get → get_by_telegram. Admin lives on the
    MAGIS ``magis_admins`` table (separate), not on
    ``Contact`` — verified by the absence of any
    ``Contact.admin`` field on the DTO."""
    book = ContactBook(factory)
    c = book.add(name="Alice", telegram_id=12345)
    assert isinstance(c, Contact)
    assert c.name == "Alice"
    # DTO surface has no admin attribute — admin is MAGIS-side.
    assert not hasattr(c, "admin") or getattr(c, "admin", None) is None

    found = book.get(contact_id=c.id)
    assert found is not None and found.telegram_id == 12345

    tg = book.get_by_telegram(telegram_id=12345)
    assert tg is not None and tg.id == c.id


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
    """Basic add → complete round-trip on the new schema.

    Schema note: the book was refactored from ``body``/``status``
    to ``description``/``completed_at`` — the open/done state
    lives on ``completed_at is None`` vs ``is not None``.
    """
    book = ActionItemBook(factory)
    item = book.add(uid=contact_id, title="x", description="y")
    assert isinstance(item, ActionItem)
    assert item.completed_at is None  # "open" == not yet completed
    completed = book.complete(action_item_id=item.id)
    assert completed is not None
    assert completed.completed_at is not None  # "done" == completed_at stamped
    refreshed = book.get(item_id=item.id)
    assert refreshed is not None
    assert refreshed.completed_at == completed.completed_at


def test_action_item_book_complete(factory, contact_id):
    """The ``complete`` primitive — pure data write.

    Authorization ("is the caller allowed to close this
    row?") lives one layer up at the tool — ``get`` then
    ``row.uid == caller`` then ``complete``. The Book
    stays a thin writer, so this test only covers:

    * happy path stamps ``completed_at`` / ``completed_by_uid``
      / ``completion_note``
    * idempotency on re-call (no overwrite of ``completed_at``
      or ``completion_note``)
    * missing ``action_item_id`` returns ``None``
    """
    book = ActionItemBook(factory)
    item = book.add(uid=contact_id, title="x", description="y")

    # Missing row → None (no exception).
    assert book.complete(action_item_id=99999) is None

    # Right path: completes, stamps completed_by_uid + note.
    completed = book.complete(
        action_item_id=item.id,
        note="done!",
        completed_by_uid=contact_id,
    )
    assert completed is not None
    assert completed.id == item.id
    assert completed.completed_at is not None
    assert completed.completed_by_uid == contact_id
    assert completed.completion_note == "done!"
    first_completed_at = completed.completed_at

    # Idempotent: second call does NOT overwrite
    # ``completed_at`` / ``completion_note``.
    again = book.complete(
        action_item_id=item.id,
        note="updated note that must NOT overwrite",
        completed_by_uid=contact_id,
    )
    assert again is not None
    assert again.completed_at == first_completed_at
    assert again.completion_note == "done!"


def test_action_item_book_complete_no_owner_check(factory, contact_id):
    """Cross-row write is the Book's job to permit — the
    tool layer refuses it via the ``get``+``uid`` check
    before reaching this primitive. This test pins the
    current behaviour so any future "add auth here" drift
    is a deliberate, visible change.
    """
    from magi.new_bus.library.local.contactBook import ContactBook

    book = ActionItemBook(factory)
    other_id = ContactBook(factory).add(name="Other").id
    # Operator A's row, but we let the caller drive the close.
    item = book.add(uid=contact_id, title="x", description="y")

    # Any caller with the id can complete; the tool's
    # ``get``+``row.uid`` check is what blocks this in
    # production. The Book is intentionally permissive.
    closed = book.complete(
        action_item_id=item.id,
        completed_by_uid=other_id,
        note="closed on someone else's behalf — auth was external",
    )
    assert closed is not None
    assert closed.completed_by_uid == other_id
    assert closed.completion_note.startswith("closed on someone else's behalf")


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


def test_tool_catalog_replace_snapshot(factory):
    sbook = ToolCatalogStateBook(factory)
    assert sbook.get() is None
    s = sbook.replace_snapshot(revision=1, snapshot_hash="abc")
    assert isinstance(s, ToolCatalogState)
    assert s.revision == 1
    s2 = sbook.replace_snapshot(revision=2, snapshot_hash="def")
    assert s2.revision == 2


def test_tool_definition_upsert(factory):
    book = ToolDefinitionBook(factory)
    d = ToolDefinition(
        name="echo", source="builtin", description="echoes",
        input_schema={"x": 1},
    )
    book.upsert_many(definitions=[d], source="builtin")
    rows = book.list_enabled()
    assert len(rows) == 1
    assert rows[0].name == "echo"
    assert rows[0].input_schema == {"x": 1}

    # upsert again — should update in place
    d2 = ToolDefinition(
        name="echo", source="builtin", description="echoes v2",
        input_schema={"x": 2},
    )
    book.upsert_many(definitions=[d2], source="builtin")
    rows = book.list_enabled()
    assert len(rows) == 1
    assert rows[0].description == "echoes v2"
    assert rows[0].input_schema == {"x": 2}


# -- TaskBook + TaskRunBook (unified user-task + preset Book) --------


def test_task_book_lifecycle(factory, contact_id):
    """One ``TaskBook`` carries BOTH user tasks
    (``source=SOURCE_USER``) and preset templates
    (``source=SOURCE_PROACTIVE``). User task links to a
    preset via ``preset_id`` / ``preset_key`` — same table,
    same Book.
    """
    tbook = TaskBook(factory)

    # Preset row: source=SOURCE_PROACTIVE, structured
    # frequency/hour/minute, no uid.
    preset = tbook.add(
        id="p1",
        name="Daily-preset",
        prompt="preset prompt",
        key="daily",
        frequency="daily",
        hour=9,
        minute=0,
        target_channel="webui",
        source=SOURCE_PROACTIVE,
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    assert isinstance(preset, Task)
    assert preset.source == SOURCE_PROACTIVE
    assert preset.key == "daily"
    assert preset.uid is None

    # User task row: source=SOURCE_USER, cron string,
    # owned by a contact, links back to the preset.
    t = tbook.add(
        id="t1",
        name="MyTask",
        prompt="do",
        cron="0 9 * * *",
        uid=contact_id,
        target_channel="webui",
        preset_id="p1",
        preset_key="daily",
        source=SOURCE_USER,
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    assert isinstance(t, Task)
    assert t.source == SOURCE_USER
    assert t.preset_id == "p1"
    assert t.preset_key == "daily"
    assert t.uid == contact_id

    # list_for_owner: only SOURCE_USER rows owned by uid.
    owned = tbook.list_for_owner(uid=contact_id)
    assert len(owned) == 1
    assert owned[0].id == "t1"

    # list_presets: only SOURCE_PROACTIVE enabled rows.
    presets = tbook.list_presets()
    assert len(presets) == 1
    assert presets[0].id == "p1"
    assert presets[0].source == SOURCE_PROACTIVE

    # Run lifecycle — unchanged.
    rbook = TaskRunBook(factory)
    r = rbook.add(
        id="r1", task_id="t1", trigger="manual",
        started_at="2026-08-05T09:00:00Z", status="running",
    )
    assert isinstance(r, TaskRun)
    rbook.complete(
        run_id="r1", status="success",
        finished_at="2026-08-05T09:01:00Z",
    )
    assert rbook.get(run_id="r1").status == "success"


def test_task_book_add_rejects_unknown_source(factory):
    """``source`` must be in ``ALL_SOURCES``. Mirrors the
    ``actionItemBook.add`` precedent — keeps the closed-set
    discipline even though the DB column is a loose
    ``String(16)``.
    """
    book = TaskBook(factory)
    with pytest.raises(ValueError, match="source must be one of"):
        book.add(
            id="t-bad",
            name="bad",
            prompt="x",
            cron="0 0 * * *",
            uid=1,
            target_channel="webui",
            source="system-external-thing",
        )


def test_channel_enum_values():
    """Pin the enum values — the dashboard and dispatcher
    treat them as a closed vocabulary, and the value strings
    land in the DB column verbatim.
    """
    assert ChannelEnum.TG == "tg"
    assert ChannelEnum.WEBUI == "webui"
    assert ChannelEnum.A2A == "a2a"
    assert ChannelEnum.SCHEDULED == "scheduled"
    # ``Channel`` is the back-compat alias (mirrors the old
    # bus shape so adapter / agent modules can migrate
    # independently).
    assert Channel is ChannelEnum
    # ``ALL_SOURCES`` is the closed set the Book validates
    # against; the action_item and task books share the
    # same constants.
    assert ALL_SOURCES == frozenset({SOURCE_USER, SOURCE_PROACTIVE})


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
    "TaskRun",
    "TaskRunBook",
    "TokenUsageBook",
    "ToolCatalogState",
    "ToolCatalogStateBook",
    "ToolDefinition",
    "ToolDefinitionBook",
]
