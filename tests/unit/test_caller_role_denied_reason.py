"""Tests for :meth:`Tool.gate` — the centralised role check
that replaced the per-module ``_gate()`` helpers and
:func:`caller_role_denied_reason`.

Six surfaces pinned:

  - ``uid`` non-integer → refuse with a
    ``"uid ... is not a valid id"`` message
    (preserves the original wording so existing tests
    that grep the error string keep working).
  - ``uid == 0`` → refuse with the
    "got 0; caller did not authenticate" message —
    catches a tooling future-bug where the loop's
    placeholder ``uid=0`` leaks through.
  - ``ctx.bus is None`` → refuse with the "no bus"
    message (covers the MCP-side callers until MCP
    migrates to new_bus).
  - Contact row missing in DB → refuse with
    ``"contact <id> not found"``.
  - Contact role not in ``ALLOWED_ROLES`` → refuse
    with the role repr (``role 'guest'``) so callers
    can keep grepping the error.
  - Happy path (role ∈ ALLOWED_ROLES) → return ``None``.

Uses an in-memory stub bus rather than booting the
real new_bus — the gate just needs ``contacts_book.get``;
the rest of the facade is irrelevant to the test.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from magi.tools.base import Tool, ToolContext, ToolResult


# -- stubs ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StubContact:
    """Minimal Contact-shaped object — only ``role`` matters
    for the gate (admin was deliberately removed per the
    2024 split, see :class:`Tool.gate`)."""

    role: str


class _StubContactsBook:
    def __init__(self, by_id: dict[int, _StubContact]):
        self._by_id = by_id

    def get(self, *, contact_id: int) -> _StubContact | None:
        return self._by_id.get(contact_id)


@dataclass(frozen=True, slots=True)
class _StubBus:
    contacts_book: _StubContactsBook


# -- the tool under test --------------------------------------------------


class _DemoTool(Tool):
    name = "demo"
    ALLOWED_ROLES = frozenset({"assigned"})
    description = ""
    input_schema: dict = {}

    async def run(self, ctx, **kwargs):
        return ToolResult(content="ok")


def _ctx(uid, *, bus: _StubBus | None = None) -> ToolContext:
    """Build a ToolContext, defaulting ``bus=None`` so tests
    can drive the "no bus" branch without ceremony."""
    return ToolContext(
        workspace="",
        uid=uid,  # type: ignore[arg-type]
        channel="webui",
        bus=bus,
    )


def _bus_with(*contacts: tuple[int, str]) -> _StubBus:
    return _StubBus(
        contacts_book=_StubContactsBook(
            {uid: _StubContact(role=role) for uid, role in contacts}
        )
    )


# -- tests ---------------------------------------------------------------


def test_returns_none_for_permitted_role():
    """Happy path: ``assigned`` role passes the
    ``{assigned}`` gate."""
    tool = _DemoTool()
    bus = _bus_with((1, "assigned"))
    assert tool.gate(_ctx(1, bus=bus)) is None


def test_permits_each_role_independently():
    """Sanity: passing each role to a matching ``ALLOWED_ROLES``
    returns ``None``; an unmatched role is refused."""
    tool = _DemoTool()
    bus = _bus_with((1, "guest"))
    # role=guest, ALLOWED_ROLES={assigned} → refused
    msg = tool.gate(_ctx(1, bus=bus))
    assert msg is not None
    assert "role 'guest'" in msg


def test_rejects_non_int_uid():
    """Non-coercible ``uid`` returns an error pointing at
    the bad input — does not raise."""
    msg = _DemoTool().gate(_ctx("not-a-number"))
    assert msg is not None
    assert "is not a valid id" in msg
    assert "'not-a-number'" in msg


def test_rejects_zero_uid():
    """``uid == 0`` is the loop's placeholder for
    "no caller resolved yet" — refuse rather than letting
    the lookup silently match an unintended row."""
    msg = _DemoTool().gate(_ctx(0))
    assert msg is not None
    assert "got 0" in msg
    assert "not authenticate" in msg


def test_rejects_no_bus():
    """``ctx.bus is None`` — old-bus callers (MCP until
    migrated) — get a friendly "no bus" message."""
    msg = _DemoTool().gate(_ctx(1, bus=None))
    assert msg is not None
    assert "no bus" in msg


def test_rejects_nonexistent_contact():
    """Contact id that doesn't resolve to a row returns a
    "not found" message — distinct from the role-mismatch
    case (no leakage about why)."""
    bus = _bus_with()  # empty
    msg = _DemoTool().gate(_ctx(99999, bus=bus))
    assert msg is not None
    assert "99999" in msg
    assert "not found" in msg


def test_rejects_wrong_role():
    """Role-mismatch path: includes the role repr so
    callers / tests that grep ``"role 'guest'"`` keep
    finding it."""
    bus = _bus_with((7, "guest"))
    msg = _DemoTool().gate(_ctx(7, bus=bus))
    assert msg is not None
    assert "role 'guest'" in msg
    # Allowed list surfaces so the operator sees the
    # policy without grepping docs.
    assert "assigned" in msg


def test_empty_allowed_roles_admits_everyone():
    """``ALLOWED_ROLES = frozenset()`` means "no gate" —
    even a caller with no role / no contact row passes.
    Tools that opt out of role-gating use this."""

    class _OpenTool(Tool):
        name = "open"
        ALLOWED_ROLES = frozenset()
        description = ""
        input_schema: dict = {}

        async def run(self, ctx, **kwargs):
            return ToolResult(content="ok")

    tool = _OpenTool()
    # No bus, no contact — still passes.
    assert tool.gate(_ctx(1, bus=None)) is None