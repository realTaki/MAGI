"""Unit tests for bus Books.

Each test exercises basic CRUD on one Book.  All tests use an
in-memory SQLite via :func:`EngineFactory` to keep them isolated.
"""

from __future__ import annotations

import pytest

from magi.bus.db import EngineFactory
from magi.bus.library.local import (
    ALL_SOURCES,
    SOURCE_PROACTIVE,
    SOURCE_USER,
    ActionItem,
    ActionItemBook,
    Channel,
    ChannelEnum,
    Contact,
    ContactBook,
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
    Setting,
    SettingBook,
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
    FK can use this fixture to get a valid contact_id."""
    from magi.bus.library.local.contactBook import ContactBook

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
    m = book.add(contact_id=contact_id, kind="fact", subject="alice", body="likes cats")
    assert isinstance(m, Memory)
    assert m.contact_id == contact_id
    assert m.body == "likes cats"
    found = book.get(memory_id=m.id)
    assert found is not None and found.body == "likes cats"


def test_memory_book_list_by_owner(factory, contact_id):
    from magi.bus.library.local.contactBook import ContactBook

    book = MemoryBook(factory)
    cbook = ContactBook(factory)
    other_id = cbook.add(name="Other").id
    book.add(contact_id=contact_id, kind="fact", subject="a", body="x")
    book.add(contact_id=other_id, kind="fact", subject="b", body="y")
    assert len(book.list_by_owner(contact_id=contact_id)) == 1
    assert len(book.list_by_owner(contact_id=other_id)) == 1


def test_memory_book_full_lifecycle(factory, contact_id):
    """add → update → complete → get round-trip on
    the new keyword-only contract.

    Pins the invariants the core-memory tools depend on:

      * ``add`` returns the created DTO
      * ``update`` only accepts ``subject``/``body``/``priority``
      * ``complete`` is idempotent — second call leaves
        ``completed_at`` untouched
      * timestamps on the DTO are ISO-8601 ``Z`` strings
        (via :func:`to_iso`), matching the
        ``api/memory.py`` wire contract
    """
    from datetime import datetime

    book = MemoryBook(factory)
    created = book.add(
        contact_id=contact_id,
        kind="quick_note",
        subject="ship the deal",
        body="waiting on legal",
        priority=3,
    )
    assert isinstance(created, Memory)
    assert created.completed_at is None

    updated = book.update(
        memory_id=created.id,
        subject="ship the deal (closed)",
        body="signed by both parties",
        priority=4,
    )
    assert updated.subject == "ship the deal (closed)"
    assert updated.body == "signed by both parties"
    assert updated.priority == 4
    assert updated.id == created.id

    completed = book.complete(memory_id=created.id)
    assert completed.completed_at is not None
    first_completed_at = completed.completed_at
    again = book.complete(memory_id=created.id)
    assert again.completed_at == first_completed_at  # idempotent

    fetched = book.get(memory_id=created.id)
    assert fetched is not None
    # ISO-Z wire shape.
    assert isinstance(fetched.completed_at, str)
    assert fetched.completed_at.endswith("Z")
    # Round-trip the parsed value back to a string and
    # confirm equality — guards against accidental
    # format drift between the column default and the
    # to_iso normalisation.
    parsed = datetime.fromisoformat(fetched.completed_at.replace("Z", "+00:00"))
    assert parsed.isoformat().startswith(first_completed_at[:16])


def test_memory_book_delete_missing_id_is_noop(factory, contact_id):
    """``delete`` on a non-existent id is a successful
    no-op (returns ``False``). Mirrors the contract the
    ``delete_memory`` tool's caller depends on for
    idempotent LLM retries."""
    book = MemoryBook(factory)
    assert book.delete(memory_id=99999) is False
    # Real row still works.
    m = book.add(
        contact_id=contact_id,
        kind="fact",
        subject="x",
        body="y",
    )
    assert book.delete(memory_id=m.id) is True
    assert book.get(memory_id=m.id) is None


def test_memory_book_add_invariants(factory, contact_id):
    """The Book owns write invariants so every caller
    path (LLM-driven tool, dashboard API, future agent
    loop) gets the same validation without each
    re-implementing length checks. Each violation
    raises :class:`ValueError`."""
    import pytest

    book = MemoryBook(factory)

    # Empty / whitespace-only subject is rejected.
    with pytest.raises(ValueError, match="subject must be a non-empty"):
        book.add(contact_id=contact_id, kind="fact", subject="", body="x")
    with pytest.raises(ValueError, match="subject must be a non-empty"):
        book.add(contact_id=contact_id, kind="fact", subject="   ", body="x")

    # Subject over the column cap (200 chars) is rejected.
    with pytest.raises(ValueError, match="subject length"):
        book.add(contact_id=contact_id, kind="fact", subject="x" * 201, body="y")

    # Empty body is rejected.
    with pytest.raises(ValueError, match="body must be a non-empty"):
        book.add(contact_id=contact_id, kind="fact", subject="ok", body="")
    with pytest.raises(ValueError, match="body must be a non-empty"):
        book.add(contact_id=contact_id, kind="fact", subject="ok", body="   ")

    # Body over 8 KiB is rejected.
    with pytest.raises(ValueError, match="body length"):
        book.add(
            contact_id=contact_id,
            kind="fact",
            subject="ok",
            body="x" * (8 * 1024 + 1),
        )

    # ``kind`` must be in ALL_KINDS.
    with pytest.raises(ValueError, match="kind must be one of"):
        book.add(
            contact_id=contact_id,
            kind="weird",
            subject="ok",
            body="ok",
        )

    # ``priority`` outside 1..5 is rejected.
    with pytest.raises(ValueError, match="priority must be 1..5"):
        book.add(
            contact_id=contact_id,
            kind="fact",
            subject="ok",
            body="ok",
            priority=0,
        )
    with pytest.raises(ValueError, match="priority must be 1..5"):
        book.add(
            contact_id=contact_id,
            kind="fact",
            subject="ok",
            body="ok",
            priority=6,
        )
    # Non-int is rejected.
    with pytest.raises(ValueError, match="priority must be 1..5"):
        book.add(
            contact_id=contact_id,
            kind="fact",
            subject="ok",
            body="ok",
            priority="3",
        )


def test_memory_book_update_invariants(factory, contact_id):
    """``update`` runs the same validators as ``add``
    for each field that is touched."""
    import pytest

    book = MemoryBook(factory)
    row = book.add(
        contact_id=contact_id,
        kind="fact",
        subject="ok",
        body="ok",
        priority=3,
    )

    # Empty subject via update is rejected.
    with pytest.raises(ValueError, match="subject must be a non-empty"):
        book.update(memory_id=row.id, subject="   ")

    # Subject over 200 chars is rejected.
    with pytest.raises(ValueError, match="subject length"):
        book.update(memory_id=row.id, subject="x" * 201)

    # Empty body via update is rejected.
    with pytest.raises(ValueError, match="body must be a non-empty"):
        book.update(memory_id=row.id, body="   ")

    # Body over 8 KiB is rejected.
    with pytest.raises(ValueError, match="body length"):
        book.update(memory_id=row.id, body="x" * (8 * 1024 + 1))

    # ``priority`` outside 1..5 is rejected.
    with pytest.raises(ValueError, match="priority must be 1..5"):
        book.update(memory_id=row.id, priority=7)

    # Missing row → LookupError.
    with pytest.raises(LookupError, match="memory row"):
        book.update(memory_id=99999, subject="x")


def test_memory_book_complete_missing_id_raises_lookup(factory):
    """``complete`` raises :class:`LookupError` for a
    missing id instead of silently no-oping. The ``complete_memory``
    tool catches it via the ``get``+``contact_id`` pre-check,
    so the LookupError is the second-line defence."""
    import pytest

    from magi.bus.db import EngineFactory

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
    row = book.add(
        contact_id=contact_id,
        kind="quick_note",
        subject="orig",
        body="orig body",
        priority=2,
    )
    after = book.update(memory_id=row.id, priority=5)
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
    c = book.add(name="Alice", telegram_id=12345)
    assert isinstance(c, Contact)
    assert c.name == "Alice"
    # Contact retains its local role flag; MAGIS membership remains the
    # authority for organisation-level administration.
    assert c.admin is False

    found = book.get(contact_id=c.id)
    assert found is not None and found.telegram_id == 12345

    tg = book.get_by_telegram(telegram_id=12345)
    assert tg is not None and tg.id == c.id


def test_contact_book_keeps_password_hash_private_and_local(factory):
    book = ContactBook(factory)
    contact = book.add(name="Password Owner")

    assert book.get_password_hash(contact_id=contact.id) is None
    book.set_password_hash(contact_id=contact.id, password_hash="scrypt$hash")
    assert book.get_password_hash(contact_id=contact.id) == "scrypt$hash"
    assert book.password_contact_ids(contact_ids=[contact.id, 999]) == {contact.id}
    # The public directory DTO must never contain a credential hash.
    assert "password_hash" not in contact.to_dict()
    assert book.clear_password_hash(contact_id=contact.id) is True
    assert book.clear_password_hash(contact_id=contact.id) is False


def test_contact_note_book(factory):
    cbook = ContactBook(factory)
    nbook = ContactNoteBook(factory)
    c = cbook.add(name="Bob")
    n = nbook.add(contact_id=c.id, note="works in finance")
    assert n.contact_id == c.id
    assert len(nbook.list_for_contact(contact_id=c.id)) == 1


# -- ConversationBook + MessageBook -------------------------------------


def test_conversation_and_message(factory):
    sbook = ConversationBook(factory)
    mbook = MessageBook(factory)

    s = sbook.add(
        conversation_id="01ABC",
        delivery_address="tg:12345",
        contact_id=1,
        channel="tg",
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    assert isinstance(s, Conversation)
    assert s.conversation_id == "01ABC"

    m = mbook.add(
        conversation_id="01ABC",
        message_id="m1",
        role="user",
        text="hi",
        ts="2026-08-05T00:00:01Z",
    )
    assert isinstance(m, Message)
    msgs = mbook.list_for_conversation(conversation_id="01ABC")
    assert len(msgs) == 1
    assert msgs[0].text == "hi"


# -- McpServerBook ------------------------------------------------------


def test_mcp_server_book_basic_add_and_list(factory):
    """The minimal add → list round-trip the worker bootstraps on.

    Pins the new schema (the flat ``mcp_servers`` columns the
    worker reads via ``list_enabled()``).
    """
    book = McpServerBook(factory)
    s = book.add(
        name="gmail",
        connection_type="stdio",
        command="mcp-gmail",
    )
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
    assert inserted.args == ("--flag", "1")
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
    assert updated.args == ()
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
    """args_json / env_json / headers_json are deserialised into
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
    assert row.args == ("--flag=1", "positional")
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
    item = book.add(contact_id=contact_id, title="x", description="y")
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
    ``row.contact_id == caller`` then ``complete``. The Book
    stays a thin writer, so this test only covers:

    * happy path stamps ``completed_at`` / ``completed_by_contact_id``
      / ``completion_note``
    * idempotency on re-call (no overwrite of ``completed_at``
      or ``completion_note``)
    * missing ``action_item_id`` returns ``None``
    """
    book = ActionItemBook(factory)
    item = book.add(contact_id=contact_id, title="x", description="y")

    # Missing row → None (no exception).
    assert book.complete(action_item_id=99999) is None

    # Right path: completes, stamps completed_by_contact_id + note.
    completed = book.complete(
        action_item_id=item.id,
        note="done!",
        completed_by_contact_id=contact_id,
    )
    assert completed is not None
    assert completed.id == item.id
    assert completed.completed_at is not None
    assert completed.completed_by_contact_id == contact_id
    assert completed.completion_note == "done!"
    first_completed_at = completed.completed_at

    # Idempotent: second call does NOT overwrite
    # ``completed_at`` / ``completion_note``.
    again = book.complete(
        action_item_id=item.id,
        note="updated note that must NOT overwrite",
        completed_by_contact_id=contact_id,
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
    from magi.bus.library.local.contactBook import ContactBook

    book = ActionItemBook(factory)
    other_id = ContactBook(factory).add(name="Other").id
    # Operator A's row, but we let the caller drive the close.
    item = book.add(contact_id=contact_id, title="x", description="y")

    # Any caller with the id can complete; the tool's
    # ``get``+``row.contact_id`` check is what blocks this in
    # production. The Book is intentionally permissive.
    closed = book.complete(
        action_item_id=item.id,
        completed_by_contact_id=other_id,
        note="closed on someone else's behalf — auth was external",
    )
    assert closed is not None
    assert closed.completed_by_contact_id == other_id
    assert closed.completion_note.startswith("closed on someone else's behalf")


def test_action_item_book_add_invariants(factory, contact_id):
    """The Book owns write invariants — title non-empty,
    column-length caps, enum membership — so every caller
    path gets the same validation without re-implementing
    them. Each violation must raise ``ValueError`` (the
    tool worker / dashboard API catch and surface as
    ``is_error=True`` / 4xx).
    """
    book = ActionItemBook(factory)

    # Empty / whitespace-only title is rejected.
    with pytest.raises(ValueError, match="title must be a non-empty"):
        book.add(contact_id=contact_id, title="")
    with pytest.raises(ValueError, match="title must be a non-empty"):
        book.add(contact_id=contact_id, title="   ")

    # Title over the column cap (200 chars) is rejected.
    with pytest.raises(ValueError, match="title length"):
        book.add(contact_id=contact_id, title="x" * 201)

    # Description over 1000 chars is rejected.
    with pytest.raises(ValueError, match="description length"):
        book.add(contact_id=contact_id, title="ok", description="d" * 1001)

    # target_url over 500 chars is rejected.
    with pytest.raises(ValueError, match="target_url length"):
        book.add(contact_id=contact_id, title="ok", target_url="u" * 501)

    # priority must be in ALL_PRIORITIES.
    with pytest.raises(ValueError, match="priority must be one of"):
        book.add(contact_id=contact_id, title="ok", priority="urgent")
    # priority "normal" (default) and "high" both pass.
    a = book.add(contact_id=contact_id, title="a", priority="normal")
    assert a.priority == "normal"
    b = book.add(contact_id=contact_id, title="b", priority="high")
    assert b.priority == "high"

    # source must be in ALL_SOURCES.
    with pytest.raises(ValueError, match="source must be one of"):
        book.add(contact_id=contact_id, title="ok", source="system")
    c = book.add(contact_id=contact_id, title="c", source="user")
    assert c.source == "user"
    d = book.add(contact_id=contact_id, title="d", source="proactive")
    assert d.source == "proactive"


def test_action_item_book_complete_note_invariant(factory, contact_id):
    """``complete`` enforces ``completion_note`` ≤500 chars
    regardless of who calls it (tool, API, future agent)."""
    book = ActionItemBook(factory)
    item = book.add(contact_id=contact_id, title="x")

    # Note at exactly the cap is fine.
    ok = book.complete(
        action_item_id=item.id,
        note="n" * 500,
        completed_by_contact_id=contact_id,
    )
    assert ok is not None

    # Note one over the cap raises.
    item2 = book.add(contact_id=contact_id, title="y")
    with pytest.raises(ValueError, match="completion_note length"):
        book.complete(
            action_item_id=item2.id,
            note="n" * 501,
            completed_by_contact_id=contact_id,
        )


# -- TokenUsageBook ----------------------------------------------------


def test_token_usage_book(factory, contact_id):
    book = TokenUsageBook(factory)
    book.add(
        contact_id=contact_id, provider="openai", model="gpt-4", input_tokens=10, output_tokens=20
    )
    book.add(
        contact_id=contact_id, provider="openai", model="gpt-4", input_tokens=5, output_tokens=10
    )
    book.add(
        contact_id=contact_id,
        provider="openai",
        model="gpt-4",
        llm_attempt_id="attempt-1",
        input_tokens=100,
        output_tokens=200,
    )
    rows = book.list_for_owner(contact_id=contact_id)
    assert len(rows) == 3
    attempt = next(row for row in rows if row.llm_attempt_id == "attempt-1")
    assert attempt.input_tokens == 100
    assert attempt.output_tokens == 200


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
    (``source=SOURCE_USER``) and preset templates
    (``source=SOURCE_PROACTIVE``) on the same table. The
    Book deliberately keeps no per-row ``preset_id`` /
    ``preset_key`` linkage — the dashboard / LLM tool pair
    templates and user tasks by overlap (name / cron /
    target_channel) outside the Book.

    Schedule is unified: every row carries either
    ``cron`` (recurring) or ``run_at`` (one-shot), never
    the structured ``frequency`` / ``hour`` / etc.
    """
    tbook = TaskBook(factory)

    # Preset row: source=SOURCE_PROACTIVE, cron string
    # (caller converted the LLM-facing structured form
    # via ``preset_to_cron`` before reaching the Book),
    # no contact_id.
    preset = tbook.add(
        id="p1",
        name="Daily-preset",
        prompt="preset prompt",
        key="daily",
        cron="0 9 * * *",
        target_channel="webui",
        source=SOURCE_PROACTIVE,
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    assert isinstance(preset, Task)
    assert preset.source == SOURCE_PROACTIVE
    assert preset.key == "daily"
    assert preset.contact_id is None
    assert preset.cron == "0 9 * * *"
    assert preset.run_at is None

    # User task row: source=SOURCE_USER, cron string,
    # owned by a contact. No ``preset_id`` / ``preset_key``
    # linkage — the relationship between a user task and
    # the proactive template that inspired it lives in the
    # caller's head (e.g., the dashboard pairs them by
    # ``name`` / cron overlap); the Book doesn't persist it.
    t = tbook.add(
        id="t1",
        name="MyTask",
        prompt="do",
        cron="0 9 * * *",
        contact_id=contact_id,
        target_channel="webui",
        source=SOURCE_USER,
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    assert isinstance(t, Task)
    assert t.source == SOURCE_USER
    assert t.contact_id == contact_id

    # list_by_user: only SOURCE_USER rows owned by contact_id.
    owned = tbook.list_by_user(contact_id=contact_id)
    assert len(owned) == 1
    assert owned[0].id == "t1"

    # list_proactive_tasks: contact_id-scoped; system preset
    # (contact_id IS NULL) visible to every contact_id.
    presets = tbook.list_proactive_tasks(contact_id=contact_id)
    assert len(presets) == 1
    assert presets[0].id == "p1"
    assert presets[0].source == SOURCE_PROACTIVE

    # list_enabled: per-user only — same contact_id.
    enabled = tbook.list_enabled(contact_id=contact_id)
    assert len(enabled) == 1
    assert enabled[0].id == "t1"

    # disable is owner-scoped: other contact_id returns False.
    other_id = contact_id + 999
    assert tbook.disable(task_id="t1", contact_id=other_id) is False
    assert tbook.get(task_id="t1").enabled == 1  # unchanged
    # right contact_id flips it; row is now disabled.
    assert tbook.disable(task_id="t1", contact_id=contact_id) is True
    assert tbook.get(task_id="t1").enabled == 0
    # post-disable, list_enabled no longer surfaces it.
    assert tbook.list_enabled(contact_id=contact_id) == []

    # Run lifecycle — unchanged.
    rbook = TaskRunBook(factory)
    r = rbook.add(
        id="r1",
        task_id="t1",
        trigger="manual",
        started_at="2026-08-05T09:00:00Z",
        status="running",
    )
    assert isinstance(r, TaskRun)
    rbook.complete(
        id="r1",
        status="success",
        finished_at="2026-08-05T09:01:00Z",
    )
    assert rbook.get(id="r1").status == "success"


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
            contact_id=1,
            target_channel="webui",
            source="system-external-thing",
        )


def test_task_book_add_invariants(factory, contact_id):
    """Write invariants the Book owns so any caller
    (LLM-driven tool, dashboard API, future agent loop)
    gets the same validation:

    * ``name`` non-empty + ≤120 chars (mirrors ``String(120)``)
    * ``prompt`` non-empty + ≤8000 chars
    * ``target_channel`` in the closed :class:`ChannelEnum`
    * ``source`` in :data:`ALL_SOURCES`

    Each violation raises ``ValueError`` for the caller to
    translate (``ToolResult.err`` for the LLM tool, 4xx for
    the dashboard route).
    """
    book = TaskBook(factory)

    # ``name`` empty / whitespace-only is rejected.
    with pytest.raises(ValueError, match="name must be a non-empty"):
        book.add(
            id="t1",
            name="",
            prompt="p",
            cron="0 0 * * *",
            contact_id=contact_id,
            target_channel="webui",
            created_at="x",
            updated_at="x",
        )
    with pytest.raises(ValueError, match="name must be a non-empty"):
        book.add(
            id="t2",
            name="   ",
            prompt="p",
            cron="0 0 * * *",
            contact_id=contact_id,
            target_channel="webui",
            created_at="x",
            updated_at="x",
        )

    # ``name`` over the column cap is rejected.
    with pytest.raises(ValueError, match="name length"):
        book.add(
            id="t3",
            name="n" * 121,
            prompt="p",
            cron="0 0 * * *",
            contact_id=contact_id,
            target_channel="webui",
            created_at="x",
            updated_at="x",
        )

    # ``prompt`` empty / whitespace-only is rejected.
    with pytest.raises(ValueError, match="prompt must be a non-empty"):
        book.add(
            id="t4",
            name="ok",
            prompt="",
            cron="0 0 * * *",
            contact_id=contact_id,
            target_channel="webui",
            created_at="x",
            updated_at="x",
        )
    with pytest.raises(ValueError, match="prompt must be a non-empty"):
        book.add(
            id="t5",
            name="ok",
            prompt="   ",
            cron="0 0 * * *",
            contact_id=contact_id,
            target_channel="webui",
            created_at="x",
            updated_at="x",
        )

    # ``prompt`` over the cap is rejected.
    with pytest.raises(ValueError, match="prompt length"):
        book.add(
            id="t6",
            name="ok",
            prompt="p" * 8001,
            cron="0 0 * * *",
            contact_id=contact_id,
            target_channel="webui",
            created_at="x",
            updated_at="x",
        )

    # ``target_channel`` outside the closed enum is rejected.
    with pytest.raises(ValueError, match="target_channel must be one of"):
        book.add(
            id="t7",
            name="ok",
            prompt="p",
            cron="0 0 * * *",
            contact_id=contact_id,
            target_channel="web",
            created_at="x",
            updated_at="x",
        )

    # All closed-set values pass.
    for ch in ("webui", "tg", "a2a", "scheduled"):
        row = book.add(
            id=f"t-ch-{ch}",
            name=f"task-{ch}",
            prompt="p",
            cron="0 0 * * *",
            contact_id=contact_id,
            target_channel=ch,
            created_at="x",
            updated_at="x",
        )
        assert row.target_channel == ch

    # Happy path lands a row.
    happy = book.add(
        id="happy",
        name="ok-name",
        prompt="ok-prompt",
        cron="0 9 * * *",
        contact_id=contact_id,
        target_channel="webui",
        created_at="x",
        updated_at="x",
    )
    assert happy.name == "ok-name"
    assert happy.target_channel == "webui"


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


def test_task_book_schedule_xor(factory, contact_id):
    """Schedule is ONE shape: ``cron`` (recurring) XOR
    ``run_at`` (one-shot). Setting both, or neither, raises
    at the Book boundary.
    """
    book = TaskBook(factory)

    # Both set — rejected.
    with pytest.raises(ValueError, match="exactly one of cron"):
        book.add(
            id="both",
            name="both",
            prompt="p",
            cron="0 9 * * *",
            run_at="2026-12-31T00:00:00Z",
            contact_id=contact_id,
            target_channel="webui",
            created_at="x",
            updated_at="x",
        )

    # Neither set — rejected.
    with pytest.raises(ValueError, match="exactly one of cron"):
        book.add(
            id="neither",
            name="neither",
            prompt="p",
            contact_id=contact_id,
            target_channel="webui",
            created_at="x",
            updated_at="x",
        )

    # run_at alone — accepted (one-shot).
    once = book.add(
        id="once",
        name="once",
        prompt="p",
        run_at="2026-12-31T00:00:00Z",
        contact_id=contact_id,
        target_channel="webui",
        created_at="x",
        updated_at="x",
    )
    assert once.run_at == "2026-12-31T00:00:00Z"
    assert once.cron is None


def test_task_book_rejects_invalid_cron(factory, contact_id):
    """A cron string the apscheduler parser can't read is
    rejected at the Book boundary — last line of defence
    before the row lands in SQLite.
    """
    book = TaskBook(factory)
    with pytest.raises(ValueError, match="cron is not a valid expression"):
        book.add(
            id="badcron",
            name="badcron",
            prompt="p",
            cron="not a cron",
            contact_id=contact_id,
            target_channel="webui",
            created_at="x",
            updated_at="x",
        )


def test_task_book_list_proactive_uid_scoped(factory, contact_id):
    """``list_proactive_tasks(contact_id=...)`` is privacy-safe:
    system presets (``contact_id IS NULL``) visible to every contact_id;
    user-private presets (``contact_id == X``) only to X.
    """
    from magi.bus.library.local.contactBook import ContactBook

    book = TaskBook(factory)
    other_id = ContactBook(factory).add(name="Other").id

    # System preset (contact_id=None): visible to both uids.
    book.add(
        id="sys-preset",
        name="system",
        prompt="p",
        cron="0 9 * * *",
        key="sys",
        target_channel="webui",
        source=SOURCE_PROACTIVE,
        created_at="x",
        updated_at="x",
    )
    # User-private preset for contact_id only.
    book.add(
        id="priv-preset",
        name="private",
        prompt="p",
        cron="0 10 * * *",
        key="priv",
        contact_id=contact_id,
        target_channel="webui",
        source=SOURCE_PROACTIVE,
        created_at="x",
        updated_at="x",
    )

    # contact_id sees BOTH (system + own private).
    own = book.list_proactive_tasks(contact_id=contact_id)
    assert {t.id for t in own} == {"sys-preset", "priv-preset"}

    # other_id sees only the system preset.
    other = book.list_proactive_tasks(contact_id=other_id)
    assert {t.id for t in other} == {"sys-preset"}


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
    from magi.bus.library.local.conversationBook import ConversationBook

    ConversationBook(factory).add(
        conversation_id="01ABC",
        delivery_address="webui:dashboard",
        contact_id=contact_id,
        channel="webui",
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )

    # First call: insert.
    task_id_1, is_update_1 = book.upsert_by_name(
        name="daily-brief",
        prompt="summarise the dashboard",
        cron="0 9 * * *",
        run_at=None,
        delivery_to=None,
        target_channel="webui",
        contact_id=contact_id,
        conversation_id="01ABC",
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
        conversation_id="01DEF",  # different from the row's current session
        tz="UTC",
    )
    assert is_update_2 is True
    assert task_id_2 == task_id_1

    # Verify the row got refreshed, with conversation_id kept sticky.
    row = book.get(task_id=task_id_1)
    assert row is not None
    assert row.prompt == "summarise the dashboard v2"
    assert row.cron == "0 10 * * *"
    assert row.target_channel == "tg"
    assert row.conversation_id == "01ABC"  # sticky: NOT overwritten by 01DEF

    # Book invariants still fire via the insert branch —
    # inserting a third task with bad data raises.
    with pytest.raises(ValueError, match="prompt must be a non-empty"):
        book.upsert_by_name(
            name="bad",
            prompt="",
            cron="0 0 * * *",
            run_at=None,
            delivery_to=None,
            target_channel="webui",
            contact_id=contact_id,
            conversation_id="x",
            tz="UTC",
        )

    # ``get_by_name`` lookup helper.
    assert book.get_by_name(name="daily-brief").id == task_id_1
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
