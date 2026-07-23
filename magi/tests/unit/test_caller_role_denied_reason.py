"""Tests for :func:`caller_role_denied_reason`.

Five surfaces pinned:

  - ``uid`` non-integer → refuse with a
    ``"uid ... is not a valid id"`` message
    (preserves the original wording so existing tests
    that grep the error string keep working).
  - ``uid == 0`` → refuse with the
    "got 0; caller did not authenticate" message —
    catches a tooling future-bug where the loop's
    placeholder ``uid=0`` leaks through.
  - Employee row missing in DB → refuse with
    ``"employee <id> not found"``.
  - Employee role not in ``allowed_roles`` → refuse
    with the role repr (``role 'employee'``) so
    callers can keep grepping the error.
  - Happy path (role ∈ allowed_roles) → return ``None``.

Mirrors the same ``fresh_db`` / seed_employees pattern
used by ``test_action_item_tools.py`` — we need a real
ORM context so the helper's Employee lookup resolves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi.agent.db import Employee, init_orm, open_session
from magi.agent.tools.base import (
    ToolContext,
    caller_role_denied_reason,
)

# -- fixtures ---------------------------------------------------------------


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
    with open_session() as db:
        alice = Employee(
            name="Alice",
            telegram_id=8501,
            role="admin",
            provider="minimax",
            api_key="fake-key-alice",
        )
        charlie = Employee(
            name="Charlie",
            telegram_id=8503,
            role="employee",
            provider="minimax",
            api_key="fake-key-charlie",
        )
        db.add_all([alice, charlie])
        db.commit()
        db.refresh(alice)
        db.refresh(charlie)
    return {"alice": alice, "charlie": charlie}


def _ctx(state: Path, uid: object) -> ToolContext:
    """Build a ToolContext with a *raw* ``uid`` so
    tests can drive the int-parsing path with arbitrary
    inputs (strings, ``None``, ``0``)."""
    return ToolContext(
        state_dir=str(state),
        workspace=state.parent,
        
        uid=uid,  # type: ignore[arg-type]
        channel="webui",
    )


# -- tests ------------------------------------------------------------------


def test_returns_none_for_permitted_role(fresh_db, seed_employees):
    """Happy path: admin passes through the
    admin+assigned gate."""
    alice = seed_employees["alice"]
    allowed = frozenset({"admin", "assigned"})
    assert caller_role_denied_reason(_ctx(fresh_db, alice.id), allowed) is None


def test_rejects_non_int_employee_id(fresh_db):
    """Non-coercible ``uid`` returns an error
    pointing at the bad input — does not raise.
    """
    msg = caller_role_denied_reason(
        _ctx(fresh_db, "not-a-number"),
        frozenset({"admin"}),
    )
    assert msg is not None
    assert "is not a valid id" in msg
    assert "'not-a-number'" in msg


def test_rejects_zero_employee_id(fresh_db):
    """``uid == 0`` is the loop's placeholder for
    "no caller resolved yet" — refuse rather than letting
    the lookup silently match an unintended row."""
    msg = caller_role_denied_reason(
        _ctx(fresh_db, 0),
        frozenset({"admin", "assigned"}),
    )
    assert msg is not None
    assert "got 0" in msg
    assert "not authenticate" in msg


def test_rejects_nonexistent_employee(fresh_db):
    """Employee id that doesn't resolve to a row returns
    a "not found" message — distinct from the
    role-mismatch case (no leakage about why)."""
    msg = caller_role_denied_reason(
        _ctx(fresh_db, 99999),
        frozenset({"admin", "assigned"}),
    )
    assert msg is not None
    assert "99999" in msg
    assert "not found" in msg


def test_rejects_wrong_role(fresh_db, seed_employees):
    """Role-mismatch path: includes the role repr so
    callers / tests that grep ``"role 'employee'"`` keep
    finding it."""
    charlie = seed_employees["charlie"]
    msg = caller_role_denied_reason(
        _ctx(fresh_db, charlie.id),
        frozenset({"admin", "assigned"}),
    )
    assert msg is not None
    assert "role 'employee'" in msg
    # The allowed list surfaces in the message so the
    # operator sees the policy without grepping docs.
    assert "admin" in msg
    assert "assigned" in msg


def test_permits_each_role_independently(fresh_db, seed_employees):
    """Sanity: passing each acceptable role to a matching
    set returns ``None``. Mirrors the "registered Tools
    ALLOWED_ROLES allows the caller's role" path."""
    charlie = seed_employees["charlie"]
    # Charlie is ``employee`` role — should pass an
    # ``{employee}`` gate, fail an ``{admin}`` gate.
    assert caller_role_denied_reason(
        _ctx(fresh_db, charlie.id), frozenset({"employee"}),
    ) is None
    assert caller_role_denied_reason(
        _ctx(fresh_db, charlie.id), frozenset({"admin"}),
    ) is not None
