"""Tests for the action-item LLM tools.

Three surfaces pinned:

  - The role gate: only ``admin`` and ``assigned`` may run
    ``add_action_item`` / ``complete_action_item`` /
    ``list_action_item``.
    ``employee`` and ``guest`` get ``is_error=True``.
  - Per-employee privacy: ``list_action_item`` and
    ``complete_action_item`` only see rows whose ``uid``
    matches the calling operator's. Operator A querying
    by id = N where N belongs to operator B gets a
    "not found / not owned" error rather than any data
    leak.
  - Idempotency on completion: a second ``complete_action_item``
    call for the same id returns the existing row without
    bumping ``completed_at`` again.

Mirrors the fixtures in ``test_memory.py`` (per-test
``fresh_db`` + three-Employee seed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi.agent.db import ActionItem, Employee, init_orm, open_session
from magi.agent.tools.action_item import (
    AddActionItemTool,
    CompleteActionItemTool,
    ListActionItemTool,
)
from magi.agent.tools.base import ToolContext


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
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
    """Three operators covering the role taxonomy."""
    with open_session() as db:
        alice = Employee(
            name="Alice",
            telegram_id=7001,
            role="admin",
            provider="minimax",
            api_key="fake-key-alice",
        )
        bob = Employee(
            name="Bob",
            telegram_id=7002,
            role="assigned",
            provider="minimax",
            api_key="fake-key-bob",
        )
        charlie = Employee(
            name="Charlie",
            telegram_id=7003,
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
    return ToolContext(
        state_dir=str(state),
        workspace=state.parent,
        uid=employee.id,
        channel="webui",
    )


def _parse(content: str) -> dict:
    return json.loads(content)


# -- AddActionItemTool ------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_action_item_creates_row_for_admin(fresh_db, seed_employees):
    tool = AddActionItemTool()
    alice = seed_employees["alice"]
    res = await tool.run(_ctx(fresh_db, alice), title="follow up with Lily")
    assert res.is_error is False
    body = _parse(res.content)
    row = body["created"]
    assert row["uid"] == alice.id
    assert row["title"] == "follow up with Lily"
    # Per-row unique kind suffix (see AddActionItemTool for the
    # rationale around the partial unique index).
    assert row["kind"].startswith("llm_action_item_")
    assert row["source"] == "llm"
    assert row["priority"] == "normal"

    from sqlalchemy import select
    with open_session() as db:
        rows = list(db.scalars(select(ActionItem)).all())
    assert len(rows) == 1
    assert rows[0].title == "follow up with Lily"


@pytest.mark.asyncio
async def test_add_action_item_creates_row_for_assigned(fresh_db, seed_employees):
    tool = AddActionItemTool()
    bob = seed_employees["bob"]
    res = await tool.run(_ctx(fresh_db, bob), title="bob's reminder")
    assert res.is_error is False
    body = _parse(res.content)
    assert body["created"]["uid"] == bob.id


@pytest.mark.asyncio
async def test_add_action_item_returns_error_for_employee_role(
    fresh_db, seed_employees,
):
    tool = AddActionItemTool()
    charlie = seed_employees["charlie"]
    res = await tool.run(_ctx(fresh_db, charlie), title="should fail")
    assert res.is_error is True
    assert "role 'employee'" in res.content


@pytest.mark.asyncio
async def test_add_action_item_missing_title_is_error(fresh_db, seed_employees):
    tool = AddActionItemTool()
    alice = seed_employees["alice"]
    res = await tool.run(_ctx(fresh_db, alice))
    assert res.is_error is True
    assert "title is required" in res.content


@pytest.mark.asyncio
async def test_add_action_item_rejects_oversized_title(fresh_db, seed_employees):
    tool = AddActionItemTool()
    alice = seed_employees["alice"]
    res = await tool.run(_ctx(fresh_db, alice), title="x" * 201)
    assert res.is_error is True
    assert "too long" in res.content


@pytest.mark.asyncio
async def test_add_action_item_high_priority(fresh_db, seed_employees):
    tool = AddActionItemTool()
    alice = seed_employees["alice"]
    res = await tool.run(
        _ctx(fresh_db, alice), title="urgent", priority="high",
    )
    body = _parse(res.content)
    assert body["created"]["priority"] == "high"


@pytest.mark.asyncio
async def test_add_action_item_rejects_bad_priority(fresh_db, seed_employees):
    tool = AddActionItemTool()
    alice = seed_employees["alice"]
    res = await tool.run(
        _ctx(fresh_db, alice), title="x", priority="URGENT",
    )
    assert res.is_error is True
    assert "priority" in res.content


# -- CompleteActionItemTool -------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_action_item_marks_own_row(fresh_db, seed_employees):
    add_tool = AddActionItemTool()
    complete_tool = CompleteActionItemTool()
    alice = seed_employees["alice"]
    add_res = await add_tool.run(_ctx(fresh_db, alice), title="ship it")
    item_id = _parse(add_res.content)["created"]["id"]

    res = await complete_tool.run(_ctx(fresh_db, alice), item_id=item_id)
    assert res.is_error is False
    item = _parse(res.content)["item"]
    assert item["id"] == item_id
    assert item["completed_at"] is not None


@pytest.mark.asyncio
async def test_complete_action_item_is_idempotent(fresh_db, seed_employees):
    add_tool = AddActionItemTool()
    complete_tool = CompleteActionItemTool()
    alice = seed_employees["alice"]
    item_id = _parse(
        (await add_tool.run(_ctx(fresh_db, alice), title="x")).content
    )["created"]["id"]
    first = _parse(
        (await complete_tool.run(_ctx(fresh_db, alice), item_id=item_id)).content
    )
    second = _parse(
        (await complete_tool.run(_ctx(fresh_db, alice), item_id=item_id)).content
    )
    assert first["item"]["completed_at"] == second["item"]["completed_at"]


@pytest.mark.asyncio
async def test_complete_action_item_cannot_close_other_employees_row(
    fresh_db, seed_employees,
):
    add_tool = AddActionItemTool()
    complete_tool = CompleteActionItemTool()
    alice = seed_employees["alice"]
    bob = seed_employees["bob"]
    item_id = _parse(
        (await add_tool.run(_ctx(fresh_db, alice), title="alice's todo")).content
    )["created"]["id"]

    res = await complete_tool.run(_ctx(fresh_db, bob), item_id=item_id)
    assert res.is_error is True
    # Error must NOT distinguish "exists but owned by someone
    # else" from "doesn't exist" — that would let an LLM
    # enumerate other operators' ids.
    assert "not found or not owned" in res.content


@pytest.mark.asyncio
async def test_complete_action_item_rejects_missing_id(fresh_db, seed_employees):
    complete_tool = CompleteActionItemTool()
    alice = seed_employees["alice"]
    res = await complete_tool.run(_ctx(fresh_db, alice), item_id=99999)
    assert res.is_error is True
    assert "not found or not owned" in res.content


@pytest.mark.asyncio
async def test_complete_action_item_rejects_non_int_id(fresh_db, seed_employees):
    complete_tool = CompleteActionItemTool()
    alice = seed_employees["alice"]
    res = await complete_tool.run(
        _ctx(fresh_db, alice), item_id="notanint",
    )
    assert res.is_error is True
    assert "must be an integer" in res.content


@pytest.mark.asyncio
async def test_complete_action_item_rejects_for_employee_role(
    fresh_db, seed_employees,
):
    complete_tool = CompleteActionItemTool()
    charlie = seed_employees["charlie"]
    res = await complete_tool.run(_ctx(fresh_db, charlie), item_id=1)
    assert res.is_error is True
    assert "role 'employee'" in res.content


# -- ListActionItemTool -----------------------------------------------------------


@pytest.mark.asyncio
async def test_list_action_item_returns_only_own_open_rows(fresh_db, seed_employees):
    add_tool = AddActionItemTool()
    list_tool = ListActionItemTool()
    alice = seed_employees["alice"]
    bob = seed_employees["bob"]
    ctx_a = _ctx(fresh_db, alice)
    ctx_b = _ctx(fresh_db, bob)

    await add_tool.run(ctx_a, title="alice-1")
    await add_tool.run(ctx_a, title="alice-2")
    await add_tool.run(ctx_b, title="bob-1")

    res = await list_tool.run(ctx_a)
    assert res.is_error is False
    body = _parse(res.content)
    titles = [item["title"] for item in body["items"]]
    assert sorted(titles) == ["alice-1", "alice-2"]
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_list_action_item_omits_completed_by_default(fresh_db, seed_employees):
    add_tool = AddActionItemTool()
    complete_tool = CompleteActionItemTool()
    list_tool = ListActionItemTool()
    alice = seed_employees["alice"]
    ctx = _ctx(fresh_db, alice)

    open_id = _parse(
        (await add_tool.run(ctx, title="open")).content
    )["created"]["id"]
    done_id = _parse(
        (await add_tool.run(ctx, title="done")).content
    )["created"]["id"]
    await complete_tool.run(ctx, item_id=done_id)

    res = await list_tool.run(ctx)
    items = _parse(res.content)["items"]
    titles = [i["title"] for i in items]
    assert titles == ["open"]
    ids = [i["id"] for i in items]
    assert open_id in ids
    assert done_id not in ids


@pytest.mark.asyncio
async def test_list_action_item_include_completed_returns_both(
    fresh_db, seed_employees,
):
    add_tool = AddActionItemTool()
    complete_tool = CompleteActionItemTool()
    list_tool = ListActionItemTool()
    alice = seed_employees["alice"]
    ctx = _ctx(fresh_db, alice)

    await add_tool.run(ctx, title="open")
    done_id = _parse(
        (await add_tool.run(ctx, title="done")).content
    )["created"]["id"]
    await complete_tool.run(ctx, item_id=done_id)

    res = await list_tool.run(ctx, include_completed=True)
    items = _parse(res.content)["items"]
    titles = sorted([i["title"] for i in items])
    assert titles == ["done", "open"]
    assert _parse(res.content)["total"] == 2


@pytest.mark.asyncio
async def test_list_action_item_empty_when_no_rows(fresh_db, seed_employees):
    list_tool = ListActionItemTool()
    alice = seed_employees["alice"]
    res = await list_tool.run(_ctx(fresh_db, alice))
    assert res.is_error is False
    assert _parse(res.content) == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_list_action_item_rejects_for_employee_role(fresh_db, seed_employees):
    list_tool = ListActionItemTool()
    charlie = seed_employees["charlie"]
    res = await list_tool.run(_ctx(fresh_db, charlie))
    assert res.is_error is True
    assert "role 'employee'" in res.content


# -- registry role-filter behaviour -----------------------------------------


def _tool_names(schemas):
    return {s["name"] for s in schemas}


def test_admin_role_sees_all_tools(seed_employees):
    from magi.agent.tools.registry import get_tool_schemas
    names = _tool_names(get_tool_schemas(caller_role="admin"))
    assert "schedule_task" in names
    assert "add_action_item" in names
    assert "complete_action_item" in names
    assert "list_action_item" in names
    # Universal gate: built-in tools are visible to
    # admin/assigned, not to other roles.
    assert "bash" in names
    assert "add_memory" in names
    assert "read_file" in names


def test_assigned_role_sees_all_tools(seed_employees):
    from magi.agent.tools.registry import get_tool_schemas
    names = _tool_names(get_tool_schemas(caller_role="assigned"))
    assert "schedule_task" in names
    assert "add_action_item" in names
    assert "bash" in names
    assert "read_file" in names


def test_employee_role_omits_all_built_in_tools(seed_employees):
    """Universal role gate — every built-in tool is
    ``admin``/``assigned`` only. ``employee`` sees an
    EMPTY tool menu (the chat path blocks them at the
    auth gate anyway; the registry filter is the
    belt-and-suspenders)."""
    from magi.agent.tools.registry import get_tool_schemas
    names = _tool_names(get_tool_schemas(caller_role="employee"))
    # All built-ins are gone.
    assert "bash" not in names
    assert "read_file" not in names
    assert "add_memory" not in names
    assert "schedule_task" not in names
    assert "add_action_item" not in names
    # (MCP tools are intentionally permissive.)


def test_guest_role_omits_all_built_in_tools(seed_employees):
    from magi.agent.tools.registry import get_tool_schemas
    names = _tool_names(get_tool_schemas(caller_role="guest"))
    assert "bash" not in names
    assert "read_file" not in names
    assert "schedule_task" not in names
    assert "add_action_item" not in names


def test_none_role_is_permissive_by_default(seed_employees):
    """``caller_role=None`` (tests, boot-time probes)
    shows all tools — production paths always pass an
    explicit role so this branch only kicks in when
    plumbing is missing."""
    from magi.agent.tools.registry import get_tool_schemas
    names = _tool_names(get_tool_schemas())
    assert "schedule_task" in names
    assert "add_action_item" in names
    assert "bash" in names


def test_get_tool_single_lookup_respects_role(seed_employees):
    from magi.agent.tools.registry import get_tool
    # Employee can't see anything built-in (universal gate).
    assert get_tool("schedule_task", caller_role="employee") is None
    assert get_tool("bash", caller_role="employee") is None
    assert get_tool("read_file", caller_role="guest") is None
    # Admin can.
    assert get_tool("schedule_task", caller_role="admin") is not None
    assert get_tool("bash", caller_role="admin") is not None
