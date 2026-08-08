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


def test_memory_book_full_lifecycle(factory, contact_id):
    """add → update → complete → get round-trip on
    the new keyword-only contract.

    Pins the invariants the core-memory tools depend on:

      * ``add`` returns the created DTO
      * ``update`` only accepts ``subject``/``body``/``importance``
      * ``complete`` is idempotent — second call leaves
        ``completed_at`` untouched
      * timestamps on the DTO are ISO-8601 ``Z`` strings
        (via :func:`to_iso`), matching the
        ``api/memory.py`` wire contract
    """
    from datetime import datetime
    book = MemoryBook(factory)
    created = book.add(
        uid=contact_id,
        kind="ongoing",
        subject="ship the deal",
        body="waiting on legal",
        importance=3,
    )
    assert isinstance(created, Memory)
    assert created.completed_at is None

    updated = book.update(
        memory_id=created.id,
        subject="ship the deal (closed)",
        body="signed by both parties",
        importance=4,
    )
    assert updated.subject == "ship the deal (closed)"
    assert updated.body == "signed by both parties"
    assert updated.importance == 4
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
        uid=contact_id, kind="important",
        subject="x", body="y",
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
        book.add(uid=contact_id, kind="important", subject="", body="x")
    with pytest.raises(ValueError, match="subject must be a non-empty"):
        book.add(uid=contact_id, kind="important", subject="   ", body="x")

    # Subject over the column cap (200 chars) is rejected.
    with pytest.raises(ValueError, match="subject length"):
        book.add(uid=contact_id, kind="important", subject="x" * 201, body="y")

    # Empty body is rejected.
    with pytest.raises(ValueError, match="body must be a non-empty"):
        book.add(uid=contact_id, kind="important", subject="ok", body="")
    with pytest.raises(ValueError, match="body must be a non-empty"):
        book.add(uid=contact_id, kind="important", subject="ok", body="   ")

    # Body over 8 KiB is rejected.
    with pytest.raises(ValueError, match="body length"):
        book.add(
            uid=contact_id, kind="important",
            subject="ok", body="x" * (8 * 1024 + 1),
        )

    # ``kind`` must be in ALL_KINDS.
    with pytest.raises(ValueError, match="kind must be one of"):
        book.add(
            uid=contact_id, kind="weird", subject="ok", body="ok",
        )

    # ``source`` must be in ALL_MEMORY_SOURCES (manual /
    # eva / system).
    with pytest.raises(ValueError, match="source must be one of"):
        book.add(
            uid=contact_id, kind="important",
            subject="ok", body="ok", source="proactive",
        )

    # ``importance`` outside 1..5 is rejected.
    with pytest.raises(ValueError, match="importance must be 1..5"):
        book.add(
            uid=contact_id, kind="important",
            subject="ok", body="ok", importance=0,
        )
    with pytest.raises(ValueError, match="importance must be 1..5"):
        book.add(
            uid=contact_id, kind="important",
            subject="ok", body="ok", importance=6,
        )
    # Non-int is rejected.
    with pytest.raises(ValueError, match="importance must be 1..5"):
        book.add(
            uid=contact_id, kind="important",
            subject="ok", body="ok", importance="3",
        )


def test_memory_book_update_invariants(factory, contact_id):
    """``update`` runs the same validators as ``add``
    for each field that is touched."""
    import pytest

    book = MemoryBook(factory)
    row = book.add(
        uid=contact_id, kind="important",
        subject="ok", body="ok", importance=3,
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

    # ``importance`` outside 1..5 is rejected.
    with pytest.raises(ValueError, match="importance must be 1..5"):
        book.update(memory_id=row.id, importance=7)

    # Missing row → LookupError.
    with pytest.raises(LookupError, match="memory row"):
        book.update(memory_id=99999, subject="x")


def test_memory_book_complete_missing_id_raises_lookup(factory):
    """``complete`` raises :class:`LookupError` for a
    missing id (unlike the legacy ``mark_completed``
    which silently no-ops). The ``complete_memory``
    tool catches it via the ``get``+``uid`` pre-check,
    so the LookupError is the second-line defence."""
    import pytest
    from magi.new_bus.db import EngineFactory
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
        uid=contact_id, kind="ongoing",
        subject="orig", body="orig body", importance=2,
    )
    after = book.update(memory_id=row.id, importance=5)
    assert after.subject == "orig"
    assert after.body == "orig body"
    assert after.importance == 5
    assert after.kind == "ongoing"  # immutable


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
        book.add(uid=contact_id, title="")
    with pytest.raises(ValueError, match="title must be a non-empty"):
        book.add(uid=contact_id, title="   ")

    # Title over the column cap (200 chars) is rejected.
    with pytest.raises(ValueError, match="title length"):
        book.add(uid=contact_id, title="x" * 201)

    # Description over 1000 chars is rejected.
    with pytest.raises(ValueError, match="description length"):
        book.add(uid=contact_id, title="ok", description="d" * 1001)

    # target_url over 500 chars is rejected.
    with pytest.raises(ValueError, match="target_url length"):
        book.add(uid=contact_id, title="ok", target_url="u" * 501)

    # priority must be in ALL_PRIORITIES.
    with pytest.raises(ValueError, match="priority must be one of"):
        book.add(uid=contact_id, title="ok", priority="urgent")
    # priority "normal" (default) and "high" both pass.
    a = book.add(uid=contact_id, title="a", priority="normal")
    assert a.priority == "normal"
    b = book.add(uid=contact_id, title="b", priority="high")
    assert b.priority == "high"

    # source must be in ALL_SOURCES.
    with pytest.raises(ValueError, match="source must be one of"):
        book.add(uid=contact_id, title="ok", source="system")
    c = book.add(uid=contact_id, title="c", source="user")
    assert c.source == "user"
    d = book.add(uid=contact_id, title="d", source="proactive")
    assert d.source == "proactive"


def test_action_item_book_complete_note_invariant(factory, contact_id):
    """``complete`` enforces ``completion_note`` ≤500 chars
    regardless of who calls it (tool, API, future agent)."""
    book = ActionItemBook(factory)
    item = book.add(uid=contact_id, title="x")

    # Note at exactly the cap is fine.
    ok = book.complete(
        action_item_id=item.id,
        note="n" * 500,
        completed_by_uid=contact_id,
    )
    assert ok is not None

    # Note one over the cap raises.
    item2 = book.add(uid=contact_id, title="y")
    with pytest.raises(ValueError, match="completion_note length"):
        book.complete(
            action_item_id=item2.id,
            note="n" * 501,
            completed_by_uid=contact_id,
        )


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
    # no uid.
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
    assert preset.uid is None
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
        uid=contact_id,
        target_channel="webui",
        source=SOURCE_USER,
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
    )
    assert isinstance(t, Task)
    assert t.source == SOURCE_USER
    assert t.uid == contact_id

    # list_by_user: only SOURCE_USER rows owned by uid.
    owned = tbook.list_by_user(uid=contact_id)
    assert len(owned) == 1
    assert owned[0].id == "t1"

    # list_proactive_tasks: uid-scoped; system preset
    # (uid IS NULL) visible to every uid.
    presets = tbook.list_proactive_tasks(uid=contact_id)
    assert len(presets) == 1
    assert presets[0].id == "p1"
    assert presets[0].source == SOURCE_PROACTIVE

    # list_enabled: per-user only — same uid.
    enabled = tbook.list_enabled(uid=contact_id)
    assert len(enabled) == 1
    assert enabled[0].id == "t1"

    # disable is owner-scoped: other uid returns False.
    other_id = contact_id + 999
    assert tbook.disable(task_id="t1", uid=other_id) is False
    assert tbook.get(task_id="t1").enabled == 1  # unchanged
    # right uid flips it; row is now disabled.
    assert tbook.disable(task_id="t1", uid=contact_id) is True
    assert tbook.get(task_id="t1").enabled == 0
    # post-disable, list_enabled no longer surfaces it.
    assert tbook.list_enabled(uid=contact_id) == []

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
        book.add(id="t1", name="", prompt="p", cron="0 0 * * *",
                 uid=contact_id, target_channel="webui",
                 created_at="x", updated_at="x")
    with pytest.raises(ValueError, match="name must be a non-empty"):
        book.add(id="t2", name="   ", prompt="p", cron="0 0 * * *",
                 uid=contact_id, target_channel="webui",
                 created_at="x", updated_at="x")

    # ``name`` over the column cap is rejected.
    with pytest.raises(ValueError, match="name length"):
        book.add(id="t3", name="n" * 121, prompt="p", cron="0 0 * * *",
                 uid=contact_id, target_channel="webui",
                 created_at="x", updated_at="x")

    # ``prompt`` empty / whitespace-only is rejected.
    with pytest.raises(ValueError, match="prompt must be a non-empty"):
        book.add(id="t4", name="ok", prompt="", cron="0 0 * * *",
                 uid=contact_id, target_channel="webui",
                 created_at="x", updated_at="x")
    with pytest.raises(ValueError, match="prompt must be a non-empty"):
        book.add(id="t5", name="ok", prompt="   ", cron="0 0 * * *",
                 uid=contact_id, target_channel="webui",
                 created_at="x", updated_at="x")

    # ``prompt`` over the cap is rejected.
    with pytest.raises(ValueError, match="prompt length"):
        book.add(id="t6", name="ok", prompt="p" * 8001, cron="0 0 * * *",
                 uid=contact_id, target_channel="webui",
                 created_at="x", updated_at="x")

    # ``target_channel`` outside the closed enum is rejected.
    with pytest.raises(ValueError, match="target_channel must be one of"):
        book.add(id="t7", name="ok", prompt="p", cron="0 0 * * *",
                 uid=contact_id, target_channel="web",
                 created_at="x", updated_at="x")

    # All closed-set values pass.
    for ch in ("webui", "tg", "a2a", "scheduled"):
        row = book.add(
            id=f"t-ch-{ch}",
            name=f"task-{ch}",
            prompt="p",
            cron="0 0 * * *",
            uid=contact_id,
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
        uid=contact_id,
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
            uid=contact_id,
            target_channel="webui",
            created_at="x", updated_at="x",
        )

    # Neither set — rejected.
    with pytest.raises(ValueError, match="exactly one of cron"):
        book.add(
            id="neither",
            name="neither",
            prompt="p",
            uid=contact_id,
            target_channel="webui",
            created_at="x", updated_at="x",
        )

    # run_at alone — accepted (one-shot).
    once = book.add(
        id="once",
        name="once",
        prompt="p",
        run_at="2026-12-31T00:00:00Z",
        uid=contact_id,
        target_channel="webui",
        created_at="x", updated_at="x",
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
            uid=contact_id,
            target_channel="webui",
            created_at="x", updated_at="x",
        )


def test_task_book_list_proactive_uid_scoped(factory, contact_id):
    """``list_proactive_tasks(uid=...)`` is privacy-safe:
    system presets (``uid IS NULL``) visible to every uid;
    user-private presets (``uid == X``) only to X.
    """
    from magi.new_bus.library.local.contactBook import ContactBook

    book = TaskBook(factory)
    other_id = ContactBook(factory).add(name="Other").id

    # System preset (uid=None): visible to both uids.
    book.add(
        id="sys-preset",
        name="system",
        prompt="p",
        cron="0 9 * * *",
        key="sys",
        target_channel="webui",
        source=SOURCE_PROACTIVE,
        created_at="x", updated_at="x",
    )
    # User-private preset for contact_id only.
    book.add(
        id="priv-preset",
        name="private",
        prompt="p",
        cron="0 10 * * *",
        key="priv",
        uid=contact_id,
        target_channel="webui",
        source=SOURCE_PROACTIVE,
        created_at="x", updated_at="x",
    )

    # contact_id sees BOTH (system + own private).
    own = book.list_proactive_tasks(uid=contact_id)
    assert {t.id for t in own} == {"sys-preset", "priv-preset"}

    # other_id sees only the system preset.
    other = book.list_proactive_tasks(uid=other_id)
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
    with ``is_update=True``. ``session_id`` is sticky on
    update (preserves conversation continuity); only
    inserts consume the caller-supplied session.

    Book invariants (length caps, channel enum) fire on
    the insert branch only — updates mutate an existing
    row that already passed those checks at insert.
    """
    book = TaskBook(factory)

    # ``session_id`` is a FK to ``chat_sessions.session_id``;
    # seed the row first so the task insert doesn't trip
    # SQLite's FK guard.
    from magi.new_bus.library.local.sessionBook import SessionBook
    SessionBook(factory).add(
        session_id="01ABC",
        delivery_address="webui:dashboard",
        uid=contact_id,
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
        uid=contact_id,
        session_id="01ABC",
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
        uid=contact_id,
        session_id="01DEF",  # different from the row's current session
        tz="UTC",
    )
    assert is_update_2 is True
    assert task_id_2 == task_id_1

    # Verify the row got refreshed, with session_id kept sticky.
    row = book.get(task_id=task_id_1)
    assert row is not None
    assert row.prompt == "summarise the dashboard v2"
    assert row.cron == "0 10 * * *"
    assert row.target_channel == "tg"
    assert row.session_id == "01ABC"  # sticky: NOT overwritten by 01DEF

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
            uid=contact_id,
            session_id="x",
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
