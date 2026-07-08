"""Tests for the per-employee long-term memory subsystem.

Four surfaces pinned:

  - :class:`MemoryStore` CRUD round-trips: ``add``,
    ``get``, ``update``, ``complete``, ``delete``,
    ``list_for_owner`` (incl. the completed-filter).
  - :func:`format_memory_block` produces a Markdown
    block with one section per kind, empty when the
    owner has no rows.
  - The four LLM tools gate on the caller's role
    (admin / assigned write; employee / guest get
    ``is_error=True``).
  - Idempotency: ``delete_memory`` of a non-existent
    id returns success (no false ``is_error``); adding
    a ``person`` row requires ``person_employee_id``.

Tests build a real SQLite via the same ``fresh_db``-style
fixture the session tests use (per-test tmp state dir,
fresh ORM engine). No LLM is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi.agent.db import Employee, init_orm, open_session
from magi.agent.memory import (
    KIND_IMPORTANT,
    KIND_ONGOING,
    KIND_PERSON,
    SCOPE_PRIMARY,
    SCOPE_SECONDARY,
    MemoryStore,
    format_memory_block,
)
from magi.agent.memory.store import MemoryView
from magi.agent.memory.tools import (
    AddMemoryTool,
    CompleteMemoryTool,
    DeleteMemoryTool,
    UpdateMemoryTool,
)
from magi.agent.tools.base import ToolContext, ToolResult


# -- fixtures ---------------------------------------------------------------


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Per-test isolated state dir + fresh ORM engine.

    Same shape as the session tests' ``fresh_db``. The
    memory tests don't need the proactive package
    imported (the engine's eager-import does that for
    us, which is fine).
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    init_orm(str(state))
    return state


@pytest.fixture
def seed_employees(fresh_db):
    """Insert two employees: alice (admin) and bob (assigned).

    The store's primary-scope memory defaults to
    ``employee_id`` = the caller. The tests use alice
    for write calls and bob to populate a
    ``person`` directory entry.
    """
    with open_session() as db:
        alice = Employee(
            name="Alice",
            telegram_id=9001,
            role="admin",
            provider="minimax",
            api_key="fake-key-alice",
        )
        bob = Employee(
            name="Bob",
            telegram_id=9002,
            role="assigned",
            provider="minimax",
            api_key="fake-key-bob",
        )
        charlie = Employee(
            name="Charlie",
            telegram_id=9003,
            role="employee",
            provider="minimax",
            api_key="fake-key-charlie",
        )
        db.add_all([alice, bob, charlie])
        db.commit()
        db.refresh(alice)
        db.refresh(bob)
        db.refresh(charlie)
    return {"alice": alice, "bob": bob, "charlie": charlie}


def _ctx(state: Path, employee: Employee) -> ToolContext:
    """Build a ToolContext for an employee."""
    return ToolContext(
        state_dir=str(state),
        workspace=state.parent,
        chat_id=str(employee.telegram_id),
        employee_id=employee.id,
        channel="webui",
    )


# -- MemoryStore -----------------------------------------------------------


def test_store_adds_important_row(fresh_db, seed_employees):
    store = MemoryStore(fresh_db)
    view = store.add(
        seed_employees["alice"].id,
        kind=KIND_IMPORTANT,
        subject="Q3 expense policy",
        body="All reimbursements need CFO sign-off.",
    )
    assert view.id > 0
    assert view.kind == KIND_IMPORTANT
    assert view.scope == SCOPE_PRIMARY
    assert view.subject == "Q3 expense policy"
    assert view.completed_at is None


def test_store_add_person_requires_person_employee_id(fresh_db, seed_employees):
    store = MemoryStore(fresh_db)
    with pytest.raises(ValueError, match="person_employee_id"):
        store.add(
            seed_employees["alice"].id,
            kind=KIND_PERSON,
            subject="Bob",
            body="In finance.",
        )


def test_store_get_returns_none_for_missing_id(fresh_db):
    store = MemoryStore(fresh_db)
    assert store.get(99999) is None


def test_store_update_patches_mutable_fields(fresh_db, seed_employees):
    store = MemoryStore(fresh_db)
    view = store.add(
        seed_employees["alice"].id,
        kind=KIND_ONGOING,
        subject="Follow up with Acme",
        body="Initial outreach last Tuesday.",
        importance=2,
    )
    updated = store.update(
        view.id,
        body="Acme replied — pushing for a meeting next week.",
        importance=4,
    )
    assert updated.id == view.id
    assert updated.body.startswith("Acme replied")
    assert updated.importance == 4
    assert updated.subject == "Follow up with Acme"  # unchanged


def test_store_complete_marks_ongoing_done(fresh_db, seed_employees):
    store = MemoryStore(fresh_db)
    view = store.add(
        seed_employees["alice"].id,
        kind=KIND_ONGOING,
        subject="Q3 report",
        body="Due Monday.",
    )
    completed = store.complete(view.id)
    assert completed.completed_at is not None
    # Filtered out of the default list.
    listed = store.list_for_owner(seed_employees["alice"].id)
    assert all(r.id != view.id for r in listed)
    # Visible when explicitly asked.
    listed_with_done = store.list_for_owner(
        seed_employees["alice"].id, include_completed=True
    )
    assert any(r.id == view.id for r in listed_with_done)


def test_store_delete_is_idempotent(fresh_db, seed_employees):
    store = MemoryStore(fresh_db)
    view = store.add(
        seed_employees["alice"].id,
        kind=KIND_IMPORTANT,
        subject="Temp note",
        body="Will be removed.",
    )
    assert store.delete(view.id) is True
    assert store.delete(view.id) is False  # second call is a no-op
    assert store.get(view.id) is None


def test_store_list_orders_by_importance_then_recency(
    fresh_db, seed_employees
):
    store = MemoryStore(fresh_db)
    owner = seed_employees["alice"].id
    store.add(owner, kind=KIND_IMPORTANT, subject="Low", body="...", importance=1)
    store.add(owner, kind=KIND_IMPORTANT, subject="High", body="...", importance=5)
    store.add(owner, kind=KIND_IMPORTANT, subject="Mid", body="...", importance=3)

    names = [r.subject for r in store.list_for_owner(owner)]
    assert names == ["High", "Mid", "Low"]


def test_store_list_filters_by_scope(fresh_db, seed_employees):
    store = MemoryStore(fresh_db)
    owner = seed_employees["alice"].id
    store.add(
        owner, kind=KIND_PERSON,
        subject="Bob (primary)", body="...",
        scope=SCOPE_PRIMARY,
        person_employee_id=seed_employees["bob"].id,
    )
    store.add(
        owner, kind=KIND_PERSON,
        subject="Charlie (secondary)", body="...",
        scope=SCOPE_SECONDARY,
        person_employee_id=seed_employees["charlie"].id,
    )
    primary = store.list_for_owner(owner, scope=SCOPE_PRIMARY)
    secondary = store.list_for_owner(owner, scope=SCOPE_SECONDARY)
    assert {r.subject for r in primary} == {"Bob (primary)"}
    assert {r.subject for r in secondary} == {"Charlie (secondary)"}


# -- format_memory_block --------------------------------------------------


def test_format_memory_block_empty_when_no_rows():
    assert format_memory_block([]) == ""


def test_format_memory_block_groups_by_kind(fresh_db, seed_employees):
    store = MemoryStore(fresh_db)
    owner = seed_employees["alice"].id
    store.add(
        owner, kind=KIND_IMPORTANT,
        subject="Q3 expense policy",
        body="All reimbursements need CFO sign-off.",
    )
    store.add(
        owner, kind=KIND_ONGOING,
        subject="Follow up with Acme",
        body="Initial outreach last Tuesday.",
    )
    store.add(
        owner, kind=KIND_PERSON,
        subject="Bob",
        body="In finance, telegram_id 9002.",
        person_employee_id=seed_employees["bob"].id,
    )

    rows = store.list_for_owner(owner)
    block = format_memory_block(rows)
    assert "## Long-term memory" in block
    # The three section headers.
    assert "重要的事" in block
    assert "正在进行" in block
    assert "认识的人" in block
    # Each subject appears in the rendered block.
    assert "Q3 expense policy" in block
    assert "Follow up with Acme" in block
    assert "Bob" in block


# -- LLM tools -------------------------------------------------------------


def test_add_memory_tool_admin_succeeds(fresh_db, seed_employees):
    tool = AddMemoryTool()
    ctx = _ctx(fresh_db, seed_employees["alice"])
    result = asyncio_run(tool.run(
        ctx,
        kind=KIND_IMPORTANT,
        subject="Q3 expense policy",
        body="All reimbursements need CFO sign-off.",
        importance=5,
    ))
    assert not result.is_error
    assert "memory_id" not in result.content  # shape is the row JSON
    assert "Q3 expense policy" in result.content


def test_add_memory_tool_assigned_succeeds(fresh_db, seed_employees):
    tool = AddMemoryTool()
    ctx = _ctx(fresh_db, seed_employees["bob"])
    result = asyncio_run(tool.run(
        ctx,
        kind=KIND_ONGOING,
        subject="Migrate SOUL.md",
        body="Move to per-employee in C4.",
    ))
    assert not result.is_error
    assert "Migrate SOUL.md" in result.content


def test_add_memory_tool_employee_role_blocked(fresh_db, seed_employees):
    """``role=employee`` is NOT allowed to write to memory.

    Same gate as the WebUI API: only ``admin`` and
    ``assigned`` may mutate. The tool returns
    ``is_error=True`` so the LLM gets a clear
    "permission denied" instead of a silent no-op.
    """
    tool = AddMemoryTool()
    ctx = _ctx(fresh_db, seed_employees["charlie"])
    result = asyncio_run(tool.run(
        ctx,
        kind=KIND_IMPORTANT,
        subject="Sneaky",
        body="Should not land.",
    ))
    assert result.is_error
    assert "role" in result.content
    # No row written.
    store = MemoryStore(fresh_db)
    assert store.list_for_owner(seed_employees["charlie"].id) == []


def test_add_memory_tool_bad_kind_returns_error(fresh_db, seed_employees):
    tool = AddMemoryTool()
    ctx = _ctx(fresh_db, seed_employees["alice"])
    result = asyncio_run(tool.run(
        ctx,
        kind="bogus",
        subject="x",
        body="y",
    ))
    assert result.is_error


def test_update_memory_tool_patches_existing_row(fresh_db, seed_employees):
    add_tool = AddMemoryTool()
    update_tool = UpdateMemoryTool()
    ctx = _ctx(fresh_db, seed_employees["alice"])

    add_result = asyncio_run(add_tool.run(
        ctx,
        kind=KIND_ONGOING,
        subject="Migrate",
        body="Initial draft.",
    ))
    assert not add_result.is_error
    # Pull the id from the JSON the tool returned.
    new_id = _extract_id(add_result.content)

    update_result = asyncio_run(update_tool.run(
        ctx,
        memory_id=new_id,
        body="Final draft, ready to ship.",
        importance=4,
    ))
    assert not update_result.is_error
    assert "Final draft" in update_result.content


def test_complete_memory_tool_marks_done(fresh_db, seed_employees):
    add_tool = AddMemoryTool()
    complete_tool = CompleteMemoryTool()
    ctx = _ctx(fresh_db, seed_employees["alice"])

    add_result = asyncio_run(add_tool.run(
        ctx, kind=KIND_ONGOING, subject="Q3 report", body="Due Monday."
    ))
    new_id = _extract_id(add_result.content)
    complete_result = asyncio_run(complete_tool.run(ctx, memory_id=new_id))
    assert not complete_result.is_error
    # ``completed_at`` is rendered as a non-null ISO string.
    assert '"completed_at": null' not in complete_result.content


def test_delete_memory_tool_idempotent(fresh_db, seed_employees):
    tool = DeleteMemoryTool()
    ctx = _ctx(fresh_db, seed_employees["alice"])
    # Deleting a never-existed id returns success.
    result = asyncio_run(tool.run(ctx, memory_id=999999))
    assert not result.is_error
    assert '"existed": false' in result.content


# -- helpers ---------------------------------------------------------------


def asyncio_run(coro):
    """Run an async coroutine in a sync test.

    The :class:`Tool` API is async so the LLM loop
    can ``await`` it; tests run synchronously. We
    spin a fresh event loop per call to avoid
    state bleed between tests.
    """
    import asyncio
    return asyncio.run(coro)


def _extract_id(tool_content: str) -> int:
    """Pull the ``"id": N`` field out of a tool result.

    The LLM-facing result is JSON; tests can read
    the same string. We deliberately don't
    ``json.loads`` to keep the helper small and
    robust to JSON formatting tweaks.
    """
    import re
    m = re.search(r'"id":\s*(\d+)', tool_content)
    assert m, f"no id in: {tool_content!r}"
    return int(m.group(1))