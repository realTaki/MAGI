"""Unit tests for the core-memory tools.

Mirrors the ``test_new_bus_books.py`` style: a per-test
in-memory SQLite via :class:`EngineFactory`, plus a
small :class:`NewBus` stub carrying the books the
tool worker actually touches (``memory_book`` /
``contacts_book``). Tools are exercised through their
public ``run()`` method with a real :class:`ToolContext`
— the gate path is not exercised (the action-item tools
follow the same convention); role enforcement lives in
:meth:`Tool.gate` and is covered elsewhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.library.local.contactBook import ContactBook
from magi.new_bus.library.local.memoryBook import MemoryBook
from magi.tools.base import ToolContext, ToolResult
from magi.tools.memory.core_memory.add_memory import AddMemoryTool
from magi.tools.memory.core_memory.complete_memory import CompleteMemoryTool
from magi.tools.memory.core_memory.delete_memory import DeleteMemoryTool
from magi.tools.memory.core_memory.update_memory import UpdateMemoryTool


# -- minimal NewBus surface -----------------------------------------------


@dataclass
class _BusStub:
    """Stand-in for :class:`magi.new_bus.NewBus` carrying
    only the Books the core-memory tools use.

    Constructing a real NewBus in tests pulls in every
    Book + Job board (and their ORM tables). Tests want
    the smallest surface that exercises ``ctx.bus`` —
    this stub keeps the migration targeted.
    """

    memory_book: MemoryBook
    contacts_book: ContactBook

    # Tool.gate() reads ``magis_admins_book``; we don't
    # exercise the gate here, so None is safe.
    magis_admins_book: object | None = None


# -- fixtures -------------------------------------------------------------


@pytest.fixture
def factory() -> EngineFactory:
    """Fresh in-memory SQLite per test."""
    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    return f


@pytest.fixture
def bus(factory: EngineFactory) -> _BusStub:
    """A NewBus-shaped stub bound to the in-memory SQLite."""
    return _BusStub(
        memory_book=MemoryBook(factory),
        contacts_book=ContactBook(factory),
    )


@pytest.fixture
def ctx(bus: _BusStub, tmp_path: Path) -> ToolContext:
    """ToolContext with the stub bus attached."""
    return ToolContext(
        workspace=str(tmp_path),
        uid=1,
        channel="webui",
        session_id="",
        bus=bus,  # type: ignore[arg-type]
    )


@pytest.fixture
def contact_id(bus: _BusStub) -> int:
    """A contact row owned by ``ctx.uid`` (uid=1).

    The tool layer enforces per-contact privacy via a
    ``get``+``row.uid`` check; we keep the test
    operator's id in sync with this fixture so happy
    paths land on owned rows.
    """
    return bus.contacts_book.add(name="fixture-operator").id


# -- add_memory ----------------------------------------------------------


@pytest.mark.asyncio
async def test_add_memory_creates_row_and_returns_dto(
    ctx: ToolContext, bus: _BusStub, contact_id: int
) -> None:
    tool = AddMemoryTool()
    result = await tool.run(
        ctx, kind="fact", subject="contract", body="due 2026-09-30",
    )
    assert isinstance(result, ToolResult)
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["created"]["uid"] == contact_id
    assert payload["created"]["kind"] == "fact"
    assert payload["created"]["subject"] == "contract"
    assert payload["created"]["body"] == "due 2026-09-30"
    assert payload["created"]["priority"] == 3  # default
    assert payload["created"]["source"] == "eva"

    # The Book actually persisted the row.
    rows = bus.memory_book.list_by_owner(uid=contact_id)
    assert len(rows) == 1
    assert rows[0].subject == "contract"


@pytest.mark.asyncio
async def test_add_memory_missing_required_field(
    ctx: ToolContext,
) -> None:
    tool = AddMemoryTool()
    result = await tool.run(ctx, kind="fact", subject="x")
    assert result.is_error is True
    assert "add_memory requires fields" in result.content
    assert "body" in result.content


@pytest.mark.asyncio
async def test_add_memory_book_value_error_becomes_tool_error(
    ctx: ToolContext,
) -> None:
    """The Book raises ``ValueError`` on invariant
    violation (subject empty, priority out of range,
    unknown kind/source) — the tool must translate
    that to ``ToolResult.err`` so the LLM sees a
    caller-fixable prompt, not a tool.crashed."""
    tool = AddMemoryTool()
    # Empty subject.
    bad_subject = await tool.run(
        ctx, kind="fact", subject="   ", body="ok",
    )
    assert bad_subject.is_error is True
    assert "subject" in bad_subject.content
    # Unknown kind.
    bad_kind = await tool.run(
        ctx, kind="weird", subject="ok", body="ok",
    )
    assert bad_kind.is_error is True
    assert "kind" in bad_kind.content
    # Importance out of range.
    bad_pri = await tool.run(
        ctx, kind="fact", subject="ok", body="ok", priority=99,
    )
    assert bad_pri.is_error is True
    assert "priority" in bad_pri.content


@pytest.mark.asyncio
async def test_add_memory_bus_none_fails_closed(tmp_path: Path) -> None:
    """``@Tool.require_bus`` returns ``is_error=True`` when
    ``ctx.bus`` is missing."""
    ctx_no_bus = ToolContext(
        workspace=str(tmp_path), uid=1, channel="webui",
        session_id="", bus=None,
    )
    tool = AddMemoryTool()
    result = await tool.run(
        ctx_no_bus, kind="fact", subject="x", body="y",
    )
    assert result.is_error is True
    assert "no bus" in result.content


# -- complete_memory -----------------------------------------------------


@pytest.mark.asyncio
async def test_complete_memory_marks_row_done(
    ctx: ToolContext, bus: _BusStub, contact_id: int
) -> None:
    row = bus.memory_book.add(
        uid=contact_id, kind="quick_note",
        subject="ship", body="in flight",
    )
    tool = CompleteMemoryTool()
    result = await tool.run(ctx, memory_id=row.id)
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["memory"]["id"] == row.id
    assert payload["memory"]["completed_at"] is not None

    refreshed = bus.memory_book.get(memory_id=row.id)
    assert refreshed is not None
    assert refreshed.completed_at is not None

    # Idempotent: a second call returns the same row
    # without changing completed_at.
    second = await tool.run(ctx, memory_id=row.id)
    assert second.is_error is False
    again = json.loads(second.content)
    assert again["memory"]["completed_at"] == payload["memory"]["completed_at"]


@pytest.mark.asyncio
async def test_complete_memory_rejects_non_int(
    ctx: ToolContext,
) -> None:
    tool = CompleteMemoryTool()
    result = await tool.run(ctx, memory_id="17")
    assert result.is_error is True
    assert "memory_id must be int" in result.content


@pytest.mark.asyncio
async def test_complete_memory_blocks_cross_contact(
    ctx: ToolContext, bus: _BusStub, contact_id: int
) -> None:
    """Strict per-contact privacy: operator A cannot
    close operator B's row, even if they ask for the
    id. The row is missing (not 'permission denied')
    so existence isn't leaked."""
    # ``ctx.uid=1`` belongs to ``contact_id``; the row
    # belongs to a different contact.
    other_id = bus.contacts_book.add(name="other").id
    foreign = bus.memory_book.add(
        uid=other_id, kind="quick_note",
        subject="not yours", body="private",
    )
    tool = CompleteMemoryTool()
    result = await tool.run(ctx, memory_id=foreign.id)
    assert result.is_error is True
    assert "not found or not owned" in result.content

    # Foreign row is untouched.
    still_open = bus.memory_book.get(memory_id=foreign.id)
    assert still_open is not None
    assert still_open.completed_at is None


@pytest.mark.asyncio
async def test_complete_memory_missing_id(
    ctx: ToolContext,
) -> None:
    tool = CompleteMemoryTool()
    result = await tool.run(ctx, memory_id=99999)
    assert result.is_error is True
    assert "not found or not owned" in result.content


# -- delete_memory -------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_memory_removes_owned_row(
    ctx: ToolContext, bus: _BusStub, contact_id: int
) -> None:
    row = bus.memory_book.add(
        uid=contact_id, kind="fact",
        subject="x", body="y",
    )
    tool = DeleteMemoryTool()
    result = await tool.run(ctx, memory_id=row.id)
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload == {"memory_id": row.id, "existed": True}
    assert bus.memory_book.get(memory_id=row.id) is None


@pytest.mark.asyncio
async def test_delete_memory_missing_id_returns_not_found(
    ctx: ToolContext,
) -> None:
    """The tool's privacy gate swallows a missing id
    into the same ``not found or not owned`` error,
    so an LLM retry that guesses an id from a stale
    block never reveals whether the row exists."""
    tool = DeleteMemoryTool()
    result = await tool.run(ctx, memory_id=99999)
    assert result.is_error is True
    assert "not found or not owned" in result.content


@pytest.mark.asyncio
async def test_delete_memory_blocks_cross_contact(
    ctx: ToolContext, bus: _BusStub, contact_id: int
) -> None:
    other_id = bus.contacts_book.add(name="other").id
    foreign = bus.memory_book.add(
        uid=other_id, kind="fact",
        subject="not yours", body="private",
    )
    tool = DeleteMemoryTool()
    result = await tool.run(ctx, memory_id=foreign.id)
    assert result.is_error is True
    # The foreign row survives — the cross-contact
    # attempt must not delete someone else's memory.
    assert bus.memory_book.get(memory_id=foreign.id) is not None


@pytest.mark.asyncio
async def test_delete_memory_rejects_non_int(
    ctx: ToolContext,
) -> None:
    tool = DeleteMemoryTool()
    result = await tool.run(ctx, memory_id="17")
    assert result.is_error is True
    assert "memory_id must be int" in result.content


# -- update_memory -------------------------------------------------------


@pytest.mark.asyncio
async def test_update_memory_patches_owned_row(
    ctx: ToolContext, bus: _BusStub, contact_id: int
) -> None:
    row = bus.memory_book.add(
        uid=contact_id, kind="quick_note",
        subject="orig", body="orig body", priority=2,
    )
    tool = UpdateMemoryTool()
    result = await tool.run(
        ctx, memory_id=row.id,
        subject="new", priority=5,
    )
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["memory"]["id"] == row.id
    assert payload["memory"]["subject"] == "new"
    assert payload["memory"]["priority"] == 5
    # Untouched fields stay.
    assert payload["memory"]["body"] == "orig body"
    assert payload["memory"]["kind"] == "quick_note"

    refreshed = bus.memory_book.get(memory_id=row.id)
    assert refreshed is not None
    assert refreshed.subject == "new"
    assert refreshed.priority == 5


@pytest.mark.asyncio
async def test_update_memory_rejects_cross_contact(
    ctx: ToolContext, bus: _BusStub, contact_id: int
) -> None:
    other_id = bus.contacts_book.add(name="other").id
    foreign = bus.memory_book.add(
        uid=other_id, kind="fact",
        subject="orig", body="private", priority=3,
    )
    tool = UpdateMemoryTool()
    result = await tool.run(
        ctx, memory_id=foreign.id, subject="hijack",
    )
    assert result.is_error is True
    assert "not found or not owned" in result.content

    # Foreign row untouched.
    refreshed = bus.memory_book.get(memory_id=foreign.id)
    assert refreshed is not None
    assert refreshed.subject == "orig"


@pytest.mark.asyncio
async def test_update_memory_translates_value_error(
    ctx: ToolContext, bus: _BusStub, contact_id: int
) -> None:
    """Book-side invariants (subject empty, priority
    out of range, body over the cap) must surface as
    LLM-facing ``ToolResult.err`` rather than crashing."""
    row = bus.memory_book.add(
        uid=contact_id, kind="fact",
        subject="ok", body="ok", priority=3,
    )
    tool = UpdateMemoryTool()

    # Empty subject.
    bad_subj = await tool.run(
        ctx, memory_id=row.id, subject="   ",
    )
    assert bad_subj.is_error is True
    assert "subject" in bad_subj.content

    # Importance out of range.
    bad_pri = await tool.run(
        ctx, memory_id=row.id, priority=9,
    )
    assert bad_pri.is_error is True
    assert "priority" in bad_pri.content

    # Body over the cap.
    too_big = await tool.run(
        ctx, memory_id=row.id, body="x" * (8 * 1024 + 1),
    )
    assert too_big.is_error is True
    assert "body" in too_big.content


@pytest.mark.asyncio
async def test_update_memory_missing_id(
    ctx: ToolContext,
) -> None:
    tool = UpdateMemoryTool()
    result = await tool.run(
        ctx, memory_id=99999, subject="x",
    )
    assert result.is_error is True
    assert "not found or not owned" in result.content


@pytest.mark.asyncio
async def test_update_memory_rejects_non_int(
    ctx: ToolContext,
) -> None:
    tool = UpdateMemoryTool()
    result = await tool.run(ctx, memory_id="17")
    assert result.is_error is True
    assert "memory_id must be int" in result.content