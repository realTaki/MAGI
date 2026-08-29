"""Unit tests for bus Books.

Each test exercises basic CRUD on one Book.  All tests use an
in-memory SQLite via :func:`EngineFactory` to keep them isolated.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from magi.old_bus.bases.db import EngineFactory
from magi.old_bus.firmwares.books.local import (
    ActionItem,
    ActionItemBook,
    Contact,
    ContactBook,
    ContactNote,
    ContactNoteBook,
    Conversation,
    ConversationBook,
    HookSignoffBook,
    McpServer,
    McpServerBook,
    Memory,
    MemoryBook,
    Message,
    MessageBook,
    Role,
    Setting,
    SettingBook,
    Task,
    TaskBook,
    TaskRun,
    TaskRunBook,
    TaskSource,
    TokenUsage,
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


def _seeded_settings_book(factory) -> SettingBook:
    """Return a ``SettingBook`` with the standard channel set registered.

    Helper for tests that build a ``ConversationBook`` from the raw factory
    fixture — they need a ``settings_book`` wired in so
    ``ConversationBook._validate_add`` can read ``channel_options()`` and
    reject unregistered channels. Mirrors runtime bootstrap where
    :func:`magi.bus.bootstrap.build_local_bus` wires the two together.
    """
    sbook = SettingBook(factory)
    for name in ("a2a", "tg", "webui", "task"):
        sbook.register_channel(name=name)
    return sbook


@pytest.fixture
def contact_id(factory):
    """Create a contact row, return its id.  Tests that need a contact
    FK can use this fixture to get a valid contact_id."""
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook

    c = ContactBook(factory).get(ContactBook(factory).add(Contact(name='Fixture')))
    return c.id


def test_base_record_id_is_book_owned(factory) -> None:
    """The auto-increment primary key is stamped by Books, not callers.

    ``id`` is now an ordinary DTO field, so a caller *may* construct a
    record carrying one — but :meth:`BaseBook.add` refuses non-zero ids
    because it is a command that mints the key.
    """
    contact = Contact(name="Unpersisted")
    assert contact.id == 0
    book = MemoryBook(factory)
    with pytest.raises(ValueError, match="unpersisted"):
        book.add(Memory(contact_id=1, kind="fact", subject="x", body="y", id=5))


# -- SettingBook --------------------------------------------------------


def test_setting_book_get_set(factory):
    book = SettingBook(factory)
    assert book.get_value(key="system.timezone") is None
    s = book.set(key="system.timezone", value="UTC")
    assert isinstance(s, Setting)
    assert s.key == "system.timezone"
    assert s.value == "UTC"
    assert book.get_value(key="system.timezone") == "UTC"


def test_setting_book_list_and_delete(factory):
    book = SettingBook(factory)
    book.set(key="a", value="1")
    book.set(key="b", value="2")
    assert set(book.list_keys()) == {"a", "b"}
    assert book.delete_by_key(key="a") is True
    assert book.delete_by_key(key="nonexistent") is False
    assert book.list_keys() == ["b"]


def test_setting_book_register_channel_is_idempotent(factory):
    book = SettingBook(factory)

    assert book.register_channel(name="a2a") == ["a2a"]
    assert book.register_channel(name="webui") == ["a2a", "webui"]
    assert book.register_channel(name="a2a") == ["a2a", "webui"]
    assert book.channel_options() == ["a2a", "webui"]


# -- MemoryBook ---------------------------------------------------------


def test_memory_book_add_and_get(factory, contact_id):
    book = MemoryBook(factory)
    m = book.get(book.add(Memory(contact_id=contact_id, kind='fact', subject='alice', body='likes cats')))
    assert isinstance(m, Memory)
    assert m.contact_id == contact_id
    assert m.body == "likes cats"
    found = book.get(m.id)
    assert found is not None and found.body == "likes cats"


def test_base_book_add_is_a_command_and_audit_fields_are_database_owned(factory, contact_id):
    book = MemoryBook(factory)
    record_id = book.add(
        Memory(contact_id=contact_id, kind="fact", subject="alice", body="likes cats")
    )

    assert isinstance(record_id, int)
    assert record_id > 0
    stored = book.get(record_id)
    assert stored is not None and stored.id == record_id
    # ``id`` is an ordinary DTO field, but ``add`` is a command: a
    # non-zero id is refused rather than silently minted over.
    with pytest.raises(ValueError, match="unpersisted"):
        book.add(
            Memory(id=record_id, contact_id=contact_id, kind="fact", subject="x", body="y")
        )


def test_record_datetime_fields_remain_native_values() -> None:
    """Records store native dataclass values; transport validates ingress."""
    message = Message(conversation_id=1, role="user", text="hello")
    assert isinstance(message.ts, datetime)


def test_memory_book_list_by_owner(factory, contact_id):
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook

    book = MemoryBook(factory)
    cbook = ContactBook(factory)
    other_id = cbook.get(cbook.add(Contact(name='Other'))).id
    book.get(book.add(Memory(contact_id=contact_id, kind='fact', subject='a', body='x')))
    book.get(book.add(Memory(contact_id=other_id, kind='fact', subject='b', body='y')))
    assert len(book.list_by_owner(contact_id=contact_id)) == 1
    assert len(book.list_by_owner(contact_id=other_id)) == 1


def test_memory_book_full_lifecycle(factory, contact_id):
    """add → update → complete → get round-trip on
    the new keyword-only contract.

    Pins the invariants the core-memory tools depend on:

      * ``add`` returns the database-generated ID; reads are explicit
      * ``update`` accepts one complete persisted Record
      * ``complete`` is idempotent — second call leaves
        ``completed_at`` untouched
      * timestamps on the DTO remain native ``datetime`` values
    """
    from datetime import datetime

    book = MemoryBook(factory)
    created = book.get(book.add(Memory(contact_id=contact_id, kind='quick_note', subject='ship the deal', body='waiting on legal', priority=3)))
    assert isinstance(created, Memory)
    assert created.completed_at is None

    candidate = replace(
        created,
        subject="ship the deal (closed)",
        body="signed by both parties",
        priority=4,
    )
    assert book.update(candidate) is True
    updated = book.get(created.id)
    assert updated is not None
    assert updated.subject == "ship the deal (closed)"
    assert updated.body == "signed by both parties"
    assert updated.priority == 4
    assert updated.id == created.id

    completed = book.complete(memory_id=created.id)
    assert completed.completed_at is not None
    first_completed_at = completed.completed_at
    again = book.complete(memory_id=created.id)
    assert again.completed_at == first_completed_at  # idempotent

    fetched = book.get(created.id)
    assert fetched is not None
    assert isinstance(fetched.completed_at, datetime)
    assert fetched.completed_at == first_completed_at


def test_memory_book_delete_missing_id_is_noop(factory, contact_id):
    """``delete`` on a non-existent id is a successful
    no-op (returns ``False``). Mirrors the contract the
    ``delete_memory`` tool's caller depends on for
    idempotent LLM retries."""
    book = MemoryBook(factory)
    assert book.delete(99999) is False
    # Real row still works.
    m = book.get(book.add(Memory(contact_id=contact_id, kind='fact', subject='x', body='y')))
    assert book.delete(m.id) is True
    assert book.get(m.id) is None


def test_memory_record_allows_free_text(factory, contact_id):
    """Free-text memory content is not constrained by the persistence DTO."""
    book = MemoryBook(factory)
    body = "x" * (16 * 1024)
    row = book.get(book.add(Memory(contact_id=contact_id, kind='fact', subject='   ', body=body)))
    assert row is not None and row.subject == '   ' and row.body == body


def test_memory_book_update_accepts_complete_free_text_record(factory, contact_id):
    book = MemoryBook(factory)
    row = book.get(book.add(Memory(contact_id=contact_id, kind='fact', subject='ok', body='ok', priority=3)))

    candidate = replace(row, subject="   ", body="x" * (16 * 1024), priority=7)
    assert book.update(candidate) is True

    # A record which disappeared between read and write is an idempotent miss.
    object.__setattr__(row, "id", 99999)
    assert book.update(row) is False


def test_memory_book_complete_missing_id_raises_lookup(factory):
    """``complete`` raises :class:`LookupError` for a
    missing id instead of silently no-oping. The ``complete_memory``
    tool catches it via the ``get``+``contact_id`` pre-check,
    so the LookupError is the second-line defence."""
    import pytest

    from magi.old_bus.bases.db import EngineFactory

    fresh = EngineFactory("sqlite:///:memory:")
    fresh.create_all()
    book = MemoryBook(fresh)
    with pytest.raises(LookupError):
        book.complete(memory_id=99999)


def test_memory_book_update_partial_keeps_other_fields(factory, contact_id):
    """A partial ``update`` must only touch the
    fields the caller supplied — any untouched
    field round-trips through unchanged."""
    book = MemoryBook(factory)
    row = book.get(book.add(Memory(contact_id=contact_id, kind='quick_note', subject='orig', body='orig body', priority=2)))
    assert book.update(replace(row, priority=5)) is True
    after = book.get(row.id)
    assert after is not None
    assert after.subject == "orig"
    assert after.body == "orig body"
    assert after.priority == 5
    assert after.kind == "quick_note"  # immutable


# -- ContactBook + ContactNoteBook --------------------------------------


def test_contact_book_full_lifecycle(factory):
    """Add → get → get_by_telegram. Admin lives on the
    MAGIS ``magis_admins`` table (separate), not on
    ``Contact`` — verified by the absence of any
    ``Contact.admin`` field on the DTO."""
    book = ContactBook(factory)
    c = book.get(book.add(Contact(name='Alice', tgid=12345)))
    assert isinstance(c, Contact)
    assert c.name == "Alice"
    # Local contacts do not carry administrator authority.
    assert c.magis_admin_id is None

    found = book.get(c.id)
    assert found is not None and found.tgid == 12345

    tg = book.get_by_telegram(tgid=12345)
    assert tg is not None and tg.id == c.id


def test_contact_book_keeps_magis_admin_projection_local(factory):
    book = ContactBook(factory)
    projection = book.ensure_magis_admin_projection(magis_admin_id=42, display_name="Admin")

    assert projection.role == Role.GUEST
    assert projection.magis_admin_id == 42
    assert book.ensure_magis_admin_projection(magis_admin_id=42, display_name="Changed").id == projection.id
    assert "password_hash" not in projection.to_dict()


def test_contact_note_book(factory):
    cbook = ContactBook(factory)
    nbook = ContactNoteBook(factory)
    c = cbook.get(cbook.add(Contact(name='Bob')))
    n = nbook.get(nbook.add(ContactNote(contact_id=c.id, note='works in finance')))
    assert n.contact_id == c.id
    assert len(nbook.list_for_contact(contact_id=c.id)) == 1


def test_contact_note_database_rejects_unknown_kind(factory):
    from sqlalchemy.exc import IntegrityError

    cbook = ContactBook(factory)
    nbook = ContactNoteBook(factory)
    c = cbook.get(cbook.add(Contact(name='Bob')))
    with pytest.raises(IntegrityError):
        nbook.get(nbook.add(ContactNote(contact_id=c.id, note='x', kind='not-a-real-kind')))


# -- ConversationBook + MessageBook -------------------------------------


def test_conversation_and_message(factory):
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    mbook = MessageBook(factory)

    s = sbook.get(sbook.add(Conversation(delivery_address='tg:12345', contact_id=1, channel='tg')))
    assert isinstance(s, Conversation)
    assert s.id > 0  # book-managed internal id

    m = mbook.get(mbook.add(Message(conversation_id=s.id, role='user', text='hi', ts=datetime(2026, 8, 5, 0, 0, 1))))
    assert isinstance(m, Message)
    msgs = mbook.list_for_conversation(conversation_id=s.id)
    assert len(msgs) == 1
    assert msgs[0].text == "hi"


def test_conversation_list_page_returns_persisted_conversations(factory, contact_id):
    book = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    own_ids = [
        book.add(
            Conversation(
                delivery_address=f"webui:{index}",
                contact_id=contact_id,
                channel="webui",
                title=f"Conversation {index}",
            )
        )
        for index in range(2)
    ]
    book.add(Conversation(delivery_address="tg:other", contact_id=999, channel="tg"))

    items, total = book.list_page_for_owner(contact_id=contact_id, limit=50, offset=0)

    assert total == 2
    assert {item.id for item in items} == set(own_ids)
    assert all(isinstance(item, Conversation) for item in items)


def test_conversation_set_summary_writes_and_bumps(factory, contact_id):
    """`set_summary` writes summary, stamps last_compaction_at, bumps updated_at."""
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id

    result = sbook.set_summary(
        contact_id=contact_id, conversation_id=cid, summary="S0"
    )
    assert result is not None
    assert result.summary == "S0"
    assert result.last_compaction_at is not None
    # bump_updated=True by default → updated_at moves forward
    assert result.updated_at >= conv.created_at

    # Re-read via get_for_owner to confirm persistence (not just returned DTO)
    fresh = sbook.get_for_owner(contact_id=contact_id, conversation_id=cid)
    assert fresh is not None
    assert fresh.summary == "S0"
    assert fresh.last_compaction_at == result.last_compaction_at


def test_conversation_set_summary_rejects_wrong_contact_id(factory, contact_id):
    """Cross-contact guard: wrong contact_id returns None and leaves row unchanged."""
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook

    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    other_contact = ContactBook(factory).get(ContactBook(factory).add(Contact(name='Other'))).id

    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id

    result = sbook.set_summary(
        contact_id=other_contact, conversation_id=cid, summary="hijack"
    )
    assert result is None

    # Confirm row is unchanged
    fresh = sbook.get_for_owner(contact_id=contact_id, conversation_id=cid)
    assert fresh is not None
    assert fresh.summary is None
    assert fresh.last_compaction_at is None


def test_conversation_set_summary_overwrites(factory, contact_id):
    """Second call supersedes the first; last_compaction_at moves forward."""
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    conv = sbook.get(sbook.add(Conversation(delivery_address='tg:1', contact_id=contact_id, channel='tg')))
    cid = conv.id

    first = sbook.set_summary(
        contact_id=contact_id, conversation_id=cid, summary="S0"
    )
    second = sbook.set_summary(
        contact_id=contact_id, conversation_id=cid, summary="S1"
    )
    assert first is not None
    assert second is not None
    assert second.summary == "S1"
    assert second.last_compaction_at >= (first.last_compaction_at or "")


# -- ConversationBook channel-validation ---------------------------------


def test_conversation_book_add_rejects_unregistered_channel(factory):
    """``_validate_add`` rejects ``channel`` values not in
    ``settings_book.channel_options()`` before SQLAlchemy sees them.

    Pin the new contract: the channel set is dynamic — workers register at
    startup — so we don't enum-ify the column at the schema level. The
    Book enforces the contract at the write boundary instead.
    """
    from magi.old_bus.firmwares.books.local.conversationBook import ChannelNotRegisteredError

    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))  # factory fixture pre-registers a2a/tg/webui/task
    with pytest.raises(ChannelNotRegisteredError) as exc_info:
        sbook.add(
            Conversation(
                delivery_address="slack:C123",
                contact_id=1,
                channel="slack",
            )
        )
    assert exc_info.value.channel == "slack"
    assert "tg" in exc_info.value.registered


def test_conversation_book_add_accepts_registered_channel(factory):
    """Happy path: every bootstrap-registered channel (``a2a`` /
    ``tg`` / ``webui`` / ``task``) passes the boundary check.

    Includes ``"a2a"`` and ``"task"`` to pin that BUS-internal channels
    are valid conversation channels too — the operator-facing disable
    is a separate concern, see ``channels/api/tasks.py``.
    """
    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    for ch in ("a2a", "tg", "webui", "task"):
        sbook.add(Conversation(delivery_address=f"{ch}:x", contact_id=1, channel=ch))


def test_conversation_book_update_rejects_unregistered_channel(factory):
    """``_validate_add`` is the single chokepoint — ``update()`` reuses it,
    so a PATCH that swaps ``channel`` to an unregistered name also fails
    fast with the same exception. (The base class' ``update()`` invokes
    ``_validate_add`` after re-reading the row.)
    """
    from magi.old_bus.firmwares.books.local.conversationBook import ChannelNotRegisteredError

    sbook = ConversationBook(factory, settings_book=_seeded_settings_book(factory))
    conv = sbook.get(sbook.add(Conversation(delivery_address="tg:1", contact_id=1, channel="tg")))

    with pytest.raises(ChannelNotRegisteredError):
        sbook.update(replace(conv, channel="slack"))


def test_conversation_book_skips_validation_when_settings_book_missing(factory):
    """Backwards-compat: a ``ConversationBook`` constructed without a
    ``settings_book`` doesn't raise on writes. This keeps narrow test
    setups (and any future code path that doesn't go through bootstrap)
    from being broken by the new check.
    """
    sbook = ConversationBook(factory, settings_book=None)
    # ``channel="anything"`` would normally fail; without ``settings_book``
    # the Book is a permissive shim — the column is still ``str`` at the DB
    # level, so no value is rejected at the schema layer either.
    sbook.add(Conversation(delivery_address="x:1", contact_id=1, channel="anything"))


# -- McpServerBook ------------------------------------------------------


def test_mcp_server_book_basic_add_and_list(factory):
    """The minimal add → list round-trip the worker bootstraps on.

    Pins the new schema (the flat ``mcp_servers`` columns the
    worker reads via ``list_enabled()``).
    """
    book = McpServerBook(factory)
    s = book.get(book.add(McpServer(name='gmail', connection_type='stdio', command='mcp-gmail')))
    assert isinstance(s, McpServer)
    assert s.name == "gmail"
    assert s.connection_type == "stdio"
    assert s.command == "mcp-gmail"
    assert book.list_enabled()[0].name == "gmail"


def test_mcp_server_book_upsert_and_delete_by_name(factory):
    """``upsert`` covers both insert and update; ``delete_by_name``
    is idempotent (returns ``False`` for unknown rows). The
    worker relies on these primitives when a change job fires.
    """
    book = McpServerBook(factory)
    inserted = book.upsert(
        name="gmail",
        connection_type="stdio",
        command="mcp-gmail",
        args=["--flag", "1"],
        env={"TOKEN": "x"},
    )
    assert inserted.name == "gmail"
    assert inserted.command == "mcp-gmail"
    assert inserted.args == ["--flag", "1"]
    assert inserted.env == {"TOKEN": "x"}
    assert book.get_by_name(name="gmail") is not None

    # Update on the same name replaces command/args/env.
    updated = book.upsert(
        name="gmail",
        connection_type="streamable_http",
        url="https://mcp.example.com",
    )
    assert updated.connection_type == "streamable_http"
    assert updated.url == "https://mcp.example.com"
    assert updated.command is None
    assert updated.args == []
    assert updated.env == {}

    # delete_by_name returns True when the row existed.
    assert book.delete_by_name(name="gmail") is True
    assert book.delete_by_name(name="gmail") is False
    assert book.get_by_name(name="gmail") is None


def test_mcp_server_book_toggle(factory):
    book = McpServerBook(factory)
    book.upsert(
        name="gmail",
        connection_type="stdio",
        command="mcp-gmail",
    )
    flipped = book.toggle(name="gmail")
    assert flipped is not None
    assert flipped.enabled is False
    flipped_again = book.toggle(name="gmail")
    assert flipped_again is not None
    assert flipped_again.enabled is True
    # Unknown name → None.
    assert book.toggle(name="missing") is None


def test_mcp_server_book_validation(factory):
    """``upsert`` mirrors the ``McpService.upsert``
    contract: ``connection_type`` must be one of three literals;
    stdio requires a non-empty ``command``; the URL-based
    transports require a non-empty ``url``.
    """
    book = McpServerBook(factory)
    with pytest.raises(ValueError, match="connection_type must be one of"):
        book.upsert(name="bad", connection_type="grpc", command="x")
    with pytest.raises(ValueError, match="stdio servers require 'command'"):
        book.upsert(name="bad", connection_type="stdio", command="")
    with pytest.raises(ValueError, match="streamable_http servers require 'url'"):
        book.upsert(name="bad", connection_type="streamable_http", url="")


def test_mcp_server_book_dto_json_columns(factory):
    """args / env / headers JSON columns are deserialised into
    typed Python objects on the way out. Round-trips preserve
    ordering and string typing.
    """
    book = McpServerBook(factory)
    book.upsert(
        name="gmail",
        connection_type="streamable_http",
        url="https://mcp.example.com",
        args=["--flag=1", "positional"],
        env={"TOKEN": "secret"},
        headers={"X-Trace": "yes"},
    )
    row = book.get_by_name(name="gmail")
    assert row is not None
    assert row.args == ["--flag=1", "positional"]
    assert row.env == {"TOKEN": "secret"}
    assert row.headers == {"X-Trace": "yes"}


# -- ActionItemBook -----------------------------------------------------


def test_action_item_book(factory, contact_id):
    """Basic add → complete round-trip on the new schema.

    Schema note: the book was refactored from ``body``/``status``
    to ``description``/``completed_at`` — the open/done state
    lives on ``completed_at is None`` vs ``is not None``.
    """
    book = ActionItemBook(factory)
    item = book.get(book.add(ActionItem(contact_id=contact_id, title='x', description='y')))
    assert isinstance(item, ActionItem)
    assert item.completed_at is None  # "open" == not yet completed
    completed = book.complete(action_item_id=item.id)
    assert completed is not None
    assert completed.completed_at is not None  # "done" == completed_at stamped
    refreshed = book.get(item.id)
    assert refreshed is not None
    assert refreshed.completed_at == completed.completed_at


def test_action_item_book_complete(factory, contact_id):
    """The ``complete`` primitive — pure data write.

    Authorization ("is the caller allowed to close this
    row?") lives one layer up at the tool — ``get`` then
    ``row.contact_id == caller`` then ``complete``. The Book
    stays a thin writer, so this test only covers:

    * happy path stamps ``completed_at`` / ``completion_note``
    * idempotency on re-call (no overwrite of ``completed_at``
      or ``completion_note``)
    * missing ``action_item_id`` returns ``None``
    """
    book = ActionItemBook(factory)
    item = book.get(book.add(ActionItem(contact_id=contact_id, title='x', description='y')))

    # Missing row → None (no exception).
    assert book.complete(action_item_id=99999) is None

    # Right path: completes, stamps completed_at + note.
    completed = book.complete(
        action_item_id=item.id,
        note="done!",
    )
    assert completed is not None
    assert completed.id == item.id
    assert completed.completed_at is not None
    assert completed.completion_note == "done!"
    first_completed_at = completed.completed_at

    # Idempotent: second call does NOT overwrite
    # ``completed_at`` / ``completion_note``.
    again = book.complete(
        action_item_id=item.id,
        note="updated note that must NOT overwrite",
    )
    assert again is not None
    assert again.completed_at == first_completed_at
    assert again.completion_note == "done!"


def test_action_item_book_complete_no_owner_check(factory, contact_id):
    """Cross-row write is the Book's job to permit — the
    tool layer refuses it via the ``get``+``contact_id`` check
    before reaching this primitive. This test pins the
    current behaviour so any future "add auth here" drift
    is a deliberate, visible change.
    """
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook

    book = ActionItemBook(factory)
    ContactBook(factory).get(ContactBook(factory).add(Contact(name='Other')))  # prove another row exists
    # Operator A's row, but we let the caller drive the close.
    item = book.get(book.add(ActionItem(contact_id=contact_id, title='x', description='y')))

    # Any caller with the id can complete; the tool's
    # ``get``+``row.contact_id`` check is what blocks this in
    # production. The Book is intentionally permissive.
    closed = book.complete(
        action_item_id=item.id,
        note="closed on someone else's behalf — auth was external",
    )
    assert closed is not None
    assert closed.completion_note.startswith("closed on someone else's behalf")


def test_action_item_record_allows_free_text(factory, contact_id):
    """Action-item text limits belong to API/tool entry points, not the DTO."""
    book = ActionItemBook(factory)
    description = "d" * 2_000
    item = book.get(book.add(ActionItem(contact_id=contact_id, title='   ', description=description)))
    assert item is not None and item.title == '   ' and item.description == description


def test_action_item_book_complete_accepts_free_text_note(factory, contact_id):
    book = ActionItemBook(factory)
    item = book.get(book.add(ActionItem(contact_id=contact_id, title='x')))

    ok = book.complete(
        action_item_id=item.id,
        note="n" * 501,
    )
    assert ok is not None
    assert ok.completion_note == "n" * 501


# -- TokenUsageBook ----------------------------------------------------


def test_token_usage_book(factory, contact_id):
    book = TokenUsageBook(factory)
    book.get(book.add(TokenUsage(contact_id=contact_id, provider='openai', model='gpt-4', input_tokens=10, output_tokens=20)))
    book.get(book.add(TokenUsage(contact_id=contact_id, provider='openai', model='gpt-4', input_tokens=5, output_tokens=10)))
    book.get(book.add(TokenUsage(contact_id=contact_id, provider='openai', model='gpt-4', input_tokens=100, output_tokens=200)))
    rows = book.list_for_owner(contact_id=contact_id)
    assert len(rows) == 3
    assert {row.input_tokens for row in rows} == {10, 5, 100}


# -- ToolCatalogStateBook + ToolDefinitionBook -------------------------


def test_tool_catalog_replace_snapshot(factory):
    sbook = ToolCatalogStateBook(factory)
    assert sbook.get_current() is None
    s = sbook.replace_snapshot(revision=1, snapshot_hash="abc")
    assert isinstance(s, ToolCatalogState)
    assert s.revision == 1
    s2 = sbook.replace_snapshot(revision=2, snapshot_hash="def")
    assert s2.revision == 2


def test_tool_definition_upsert(factory):
    book = ToolDefinitionBook(factory)
    d = ToolDefinition(
        name="echo",
        source="builtin",
        description="echoes",
        input_schema={"x": 1},
    )
    book.upsert_many(definitions=[d], source="builtin")
    rows = book.list_enabled()
    assert len(rows) == 1
    assert rows[0].name == "echo"
    assert rows[0].input_schema == {"x": 1}

    # upsert again — should update in place
    d2 = ToolDefinition(
        name="echo",
        source="builtin",
        description="echoes v2",
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
    (``source=TaskSource.USER``) and preset templates
    (``source=TaskSource.PROACTIVE``) on the same table.

    Schedule is unified: every row carries either
    ``cron`` (recurring) or ``run_at`` (one-shot), never
    the structured ``frequency`` / ``hour`` / etc.
    """
    tbook = TaskBook(factory)

    # Preset row: source=TaskSource.PROACTIVE, cron string
    # (caller converted the LLM-facing structured form
    # via ``preset_to_cron`` before reaching the Book),
    # no contact_id.
    preset = tbook.get(tbook.add(Task(task_id='p1', name='Daily-preset', prompt='preset prompt', cron='0 9 * * *', target_channel='webui', source=TaskSource.PROACTIVE)))
    assert isinstance(preset, Task)
    assert preset.source == TaskSource.PROACTIVE
    assert preset.contact_id is None
    assert preset.cron == "0 9 * * *"
    assert preset.run_at is None

    # User task row: source=TaskSource.USER, cron string,
    # owned by a contact.
    t = tbook.get(tbook.add(Task(task_id='t1', name='MyTask', prompt='do', cron='0 9 * * *', contact_id=contact_id, target_channel='webui', source=TaskSource.USER)))
    assert isinstance(t, Task)
    assert t.source == TaskSource.USER
    assert t.contact_id == contact_id

    # list_by_user: only TaskSource.USER rows owned by contact_id.
    owned = tbook.list_by_user(contact_id=contact_id)
    assert len(owned) == 1
    assert owned[0].task_id == "t1"

    # list_proactive_tasks: contact_id-scoped; system preset
    # (contact_id IS NULL) visible to every contact_id.
    presets = tbook.list_proactive_tasks(contact_id=contact_id)
    assert len(presets) == 1
    assert presets[0].task_id == "p1"
    assert presets[0].source == TaskSource.PROACTIVE

    # list_enabled: per-user only — same contact_id.
    enabled = tbook.list_enabled(contact_id=contact_id)
    assert len(enabled) == 1
    assert enabled[0].task_id == "t1"

    # disable is owner-scoped: other contact_id returns False.
    other_id = contact_id + 999
    assert tbook.disable(task_id="t1", contact_id=other_id) is False
    assert tbook.get_by_task_id(task_id="t1").enabled == 1  # unchanged
    # right contact_id flips it; row is now disabled.
    assert tbook.disable(task_id="t1", contact_id=contact_id) is True
    assert tbook.get_by_task_id(task_id="t1").enabled == 0
    # post-disable, list_enabled no longer surfaces it.
    assert tbook.list_enabled(contact_id=contact_id) == []

    # Run lifecycle — unchanged.
    rbook = TaskRunBook(factory)
    r = rbook.get(rbook.add(TaskRun(task_id='t1', run_id='r1', manual=True, started_at=datetime.fromisoformat('2026-08-05T09:00:00Z').replace(tzinfo=None), status='running')))
    assert isinstance(r, TaskRun)
    rbook.complete(
        run_id="r1",
        status="success",
        finished_at=datetime.fromisoformat("2026-08-05T09:01:00Z").replace(tzinfo=None),
    )
    assert rbook.get_by_run_id(run_id="r1").status == "success"


def test_task_record_allows_free_text_but_book_validates_schedule(factory, contact_id):
    """Task DTO text is unconstrained; schedule semantics stay in the Book."""
    book = TaskBook(factory)
    row = book.get(book.add(Task(task_id='t1', name='   ', prompt='p' * 9000, cron='0 0 * * *', contact_id=contact_id, target_channel='webui')))
    assert row is not None and row.name == '   ' and len(row.prompt) == 9000
    with pytest.raises(ValueError, match="exactly one"):
        book.add(Task(task_id='t2', name='x', prompt='p', contact_id=contact_id, target_channel='webui'))


def test_task_target_channel_round_trips_as_registered_name(factory, contact_id):
    """Task storage does not carry its own closed channel vocabulary."""
    book = TaskBook(factory)
    task = book.get(
        book.add(
            Task(
                task_id="channel-name",
                name="channel name",
                prompt="p",
                cron="0 0 * * *",
                contact_id=contact_id,
                target_channel="custom-worker",
            )
        )
    )
    assert task is not None and task.target_channel == "custom-worker"


def test_task_source_enum_values():
    """Pin the :class:`TaskSource` enum values and verify
    the type contract survives a round-trip through the ORM.

    The DTO field ``Task.source`` is annotated :class:`TaskSource`
    so the LLM tool and dashboard can use ``isinstance`` checks;
    the DB column stays ``Text`` (enum not enforced at the DB layer). The
    ``BaseRecord.from_row`` coerces the raw string back into the
    enum, so callers see enum members on read AND on write.
    """
    from magi.old_bus.firmwares.books.local.tasksBook import TaskSource

    assert TaskSource.USER == "user"
    assert TaskSource.PROACTIVE == "proactive"
    # ``StrEnum`` keeps string equality with the raw value.
    assert TaskSource.USER == "user"
    assert TaskSource.PROACTIVE == "proactive"
    # Writing via the enum and reading back yields the enum.
    from magi.old_bus.bases.db import EngineFactory as _EF
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook

    ef = _EF("sqlite:///:memory:")
    ef.create_all()
    cbook = ContactBook(ef)
    cid = cbook.get(cbook.add(Contact(name='fixture'))).id
    book = TaskBook(ef)
    row = book.get(book.add(Task(task_id='src-enum', name='src-enum', prompt='p', cron='0 0 * * *', contact_id=cid, target_channel='webui', source=TaskSource.PROACTIVE)))
    assert isinstance(row.source, TaskSource)
    assert row.source is TaskSource.PROACTIVE
    # Round-trip via ``get`` keeps the enum.
    fetched = book.get_by_task_id(task_id="src-enum")
    assert fetched is not None
    assert isinstance(fetched.source, TaskSource)
    assert fetched.source is TaskSource.PROACTIVE
    # Raw string form (legacy callers) is also accepted via the
    # back-compat alias and returns the same enum.
    raw = book.get(book.add(Task(task_id='src-raw', name='src-raw', prompt='p', cron='0 0 * * *', contact_id=cid, target_channel='webui', source=TaskSource.USER)))
    assert raw.source is TaskSource.USER


def test_task_book_schedule_xor(factory, contact_id):
    """Schedule is ONE shape: ``cron`` (recurring) XOR
    ``run_at`` (one-shot). Setting both, or neither, raises
    at the Book boundary.
    """
    book = TaskBook(factory)

    # Both set — rejected.
    with pytest.raises(ValueError, match="exactly one of cron"):
        book.get(book.add(Task(task_id='both', name='both', prompt='p', cron='0 9 * * *', run_at=datetime(2026, 12, 31), contact_id=contact_id, target_channel='webui')))

    # Neither set — rejected.
    with pytest.raises(ValueError, match="exactly one of cron"):
        book.get(book.add(Task(task_id='neither', name='neither', prompt='p', contact_id=contact_id, target_channel='webui')))

    # run_at alone — accepted (one-shot).
    once = book.get(book.add(Task(task_id='once', name='once', prompt='p', run_at=datetime(2026, 12, 31), contact_id=contact_id, target_channel='webui')))
    assert once.run_at == datetime(2026, 12, 31)
    assert once.cron is None


def test_task_book_rejects_invalid_cron(factory, contact_id):
    """A cron string the apscheduler parser can't read is
    rejected at the Book boundary — last line of defence
    before the row lands in SQLite.
    """
    book = TaskBook(factory)
    with pytest.raises(ValueError, match="cron is not a valid expression"):
        book.get(book.add(Task(task_id='badcron', name='badcron', prompt='p', cron='not a cron', contact_id=contact_id, target_channel='webui')))


def test_task_book_list_proactive_uid_scoped(factory, contact_id):
    """``list_proactive_tasks(contact_id=...)`` is privacy-safe:
    system presets (``contact_id IS NULL``) visible to every contact_id;
    user-private presets (``contact_id == X``) only to X.
    """
    from magi.old_bus.firmwares.books.local.contactBook import ContactBook

    book = TaskBook(factory)
    other_id = ContactBook(factory).get(ContactBook(factory).add(Contact(name='Other'))).id

    # System preset (contact_id=None): visible to both uids.
    book.get(book.add(Task(task_id='sys-preset', name='system', prompt='p', cron='0 9 * * *', target_channel='webui', source=TaskSource.PROACTIVE)))
    # User-private preset for contact_id only.
    book.get(book.add(Task(task_id='priv-preset', name='private', prompt='p', cron='0 10 * * *', contact_id=contact_id, target_channel='webui', source=TaskSource.PROACTIVE)))

    # contact_id sees BOTH (system + own private).
    own = book.list_proactive_tasks(contact_id=contact_id)
    assert {t.task_id for t in own} == {"sys-preset", "priv-preset"}

    # other_id sees only the system preset.
    other = book.list_proactive_tasks(contact_id=other_id)
    assert {t.task_id for t in other} == {"sys-preset"}


# -- HookSignoffBook --------------------------------------------------


def test_hook_signoff_book_empty(factory):
    book = HookSignoffBook(factory)
    assert book.list_pending() == []


def test_task_book_upsert_by_name(factory, contact_id):
    """The ``upsert_by_name`` primitive — same shape the
    WebUI task API + ``schedule_task`` tool share. Idempotent
    on the unique ``name`` column, so LLM retries don't
    create duplicates.

    First call inserts; second call with the same name
    updates in place and returns the **same** ``task_id``
    with ``is_update=True``. ``conversation_id`` is sticky on
    update (preserves conversation continuity); only
    inserts consume the caller-supplied session.

    Book invariants (length caps, channel enum) fire on
    the insert branch only — updates mutate an existing
    row that already passed those checks at insert.
    """
    book = TaskBook(factory)

    # ``conversation_id`` is a FK to ``chat_conversations.conversation_id``;
    # seed the row first so the task insert doesn't trip
    # SQLite's FK guard.
    from magi.old_bus.firmwares.books.local.conversationBook import ConversationBook

    conv_seed = ConversationBook(factory, settings_book=_seeded_settings_book(factory)).get(ConversationBook(factory, settings_book=_seeded_settings_book(factory)).add(Conversation(delivery_address='webui:dashboard', contact_id=contact_id, channel='webui')))
    seed_cid = conv_seed.id

    # First call: insert.
    task_id_1, is_update_1 = book.upsert_by_name(
        name="daily-brief",
        prompt="summarise the dashboard",
        cron="0 9 * * *",
        run_at=None,
        delivery_to=None,
        target_channel="webui",
        contact_id=contact_id,
        conversation_id=seed_cid,
        tz="UTC",
    )
    assert is_update_1 is False
    assert isinstance(task_id_1, str) and task_id_1

    # Second call: same name → update, same id.
    task_id_2, is_update_2 = book.upsert_by_name(
        name="daily-brief",
        prompt="summarise the dashboard v2",
        cron="0 10 * * *",
        run_at=None,
        delivery_to=None,
        target_channel="tg",
        contact_id=contact_id,
        conversation_id=999,  # different from the row's current session
        tz="UTC",
    )
    assert is_update_2 is True
    assert task_id_2 == task_id_1

    # Verify the row got refreshed, with conversation_id kept sticky.
    row = book.get_by_task_id(task_id=task_id_1)
    assert row is not None
    assert row.prompt == "summarise the dashboard v2"
    assert row.cron == "0 10 * * *"
    assert row.target_channel == "tg"
    assert row.conversation_id == seed_cid  # sticky: NOT overwritten by 01DEF

    # The remaining Book invariant is foreign-key/semantic resolution.
    with pytest.raises(ValueError, match="unknown conversation_id"):
        book.upsert_by_name(
            name="bad",
            prompt="",
            cron="0 0 * * *",
            run_at=None,
            delivery_to=None,
            target_channel="webui",
            contact_id=contact_id,
            conversation_id=999,
            tz="UTC",
        )

    # ``get_by_name`` lookup helper.
    assert book.get_by_name(name="daily-brief").task_id == task_id_1
    assert book.get_by_name(name="nonexistent") is None


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
    "Conversation",
    "ConversationBook",
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
