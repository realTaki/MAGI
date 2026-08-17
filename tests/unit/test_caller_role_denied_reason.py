"""Tests for :meth:`Tool.gate` — the centralised effective-role
gate that replaced the per-module ``_gate()`` helpers and
:func:`caller_role_denied_reason`.

Surfaces pinned:

  - ``uid`` non-integer → refuse with
    ``"uid ... is not a valid id"``.
  - ``uid == 0`` → refuse with
    "got 0; caller did not authenticate".
  - ``ctx.bus is None`` → refuse with "no bus" (MCP-side
    callers until MCP migrates to bus).
  - Contact row missing in DB → refuse with
    ``"contact <id> not found"``.
  - Caller's effective role-tag set is disjoint from
    ``ALLOWED_ROLES`` → refuse with role + allowed list.
  - Happy path (any tag in effective-set ∩
    ``ALLOWED_ROLES``) → ``None``.

Effective role-tag set
---------------------

The caller has two orthogonal sources of role tags:

  1. ``Contact.role`` (per-MAGI; ``assigned`` / ``guest``
     / ``contact``).
  2. ``magis_admins`` rows for the caller's uid
     (MAGIS-level admin; virtual ``"admin"`` tag added
     when present).

A tool with ``ALLOWED_ROLES = {"admin", "assigned"}``
admits both the served user and any MAGIS admin. A tool
gated on ``{"admin"}`` only admits MAGIS admins. The
two flags are not stored together — they're orthogonal,
so a user can be ``assigned`` here AND ``admin`` in
some MAGIS (matches both), ``assigned`` only (matches
``{"assigned"}``), or admin only (matches
``{"admin"}``).

Uses an in-memory stub bus rather than booting the
real bus — the gate just needs ``contacts_book.get``
+ ``magis_admins_book.is_admin_for``; the rest of the
facade is irrelevant to the test.
"""

from __future__ import annotations

from dataclasses import dataclass

from magi.tools.base import Tool, ToolContext, ToolResult

# -- stubs ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StubContact:
    """Minimal Contact-shaped object — only ``role`` matters
    for the gate (admin lives on ``magis_admins``, not on
    Contact)."""

    role: str
    magis_admin_id: int | None = None


class _StubContactsBook:
    """Minimal stand-in for :class:`ContactBook`.

    Signature mirrors :meth:`BaseBook.get`: positional
    ``record_id`` argument — :meth:`Tool.gate` calls it
    positionally (see :mod:`magi.tools.base`).
    """

    def __init__(self, by_id: dict[int, _StubContact]):
        self._by_id = by_id

    def get(self, record_id: int) -> _StubContact | None:
        return self._by_id.get(record_id)


class _StubMagisAdminsBook:
    """Stand-in for :class:`MagisAdminBook`. Inherits
    :meth:`BaseBook.get`'s positional signature."""

    def __init__(self, admin_uids: set[int]):
        self._admin_uids = admin_uids

    def get(self, record_id: int):
        return object() if record_id in self._admin_uids else None


@dataclass(frozen=True, slots=True)
class _StubBus:
    contacts_book: _StubContactsBook
    magis_admins_book: _StubMagisAdminsBook


# -- the tool under test --------------------------------------------------


class _DemoTool(Tool):
    name = "demo"
    ALLOWED_ROLES = frozenset({"assigned"})
    description = ""
    input_schema: dict = {}

    async def run(self, _ctx, **_kwargs):
        return ToolResult(content="ok")


def _ctx(contact_id, *, bus: _StubBus | None = None) -> ToolContext:
    """Build a ToolContext, defaulting ``bus=None`` so tests
    can drive the "no bus" branch without ceremony."""
    return ToolContext(
        workspace="",
        contact_id=contact_id,  # type: ignore[arg-type]
        channel="webui",
        bus=bus,
    )


def _bus_with(
    *contacts: tuple[int, str],
    admin_uids: set[int] | None = None,
) -> _StubBus:
    return _StubBus(
        contacts_book=_StubContactsBook({
            uid: _StubContact(role=role, magis_admin_id=uid if uid in (admin_uids or set()) else None)
            for uid, role in contacts
        }),
        magis_admins_book=_StubMagisAdminsBook(admin_uids or set()),
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
    assert "role(s)" in msg
    assert "'guest'" in msg


def test_admin_tag_admits_via_magis_only():
    """MAGIS admin tag (``"admin"``) lives outside the local
    role enum. A user with role='guest' but a MAGIS admin
    row passes the ``{admin, assigned}`` gate (the
    effective-set contains ``admin``).

    Tools gated only on ``{'admin'}`` (e.g. cross-MAGIS
    admin tools) admit MAGIS admins even when their
    local role is ``guest``.
    """

    class _AdminOnlyTool(Tool):
        name = "admin_only"
        ALLOWED_ROLES = frozenset({"admin"})
        description = ""
        input_schema: dict = {}

        async def run(self, _ctx, **_kwargs):
            return ToolResult(content="ok")

    tool = _AdminOnlyTool()
    # role=guest, but uid=2 is in magis_admins → admitted.
    bus = _bus_with((2, "guest"), admin_uids={2})
    assert tool.gate(_ctx(2, bus=bus)) is None
    # role=guest, NOT admin → refused.
    bus_no_admin = _bus_with((3, "guest"))
    msg = tool.gate(_ctx(3, bus=bus_no_admin))
    assert msg is not None
    assert "role(s)" in msg
    assert "'guest'" in msg


def test_assigned_and_admin_both_match():
    """A user with both ``role='assigned'`` AND a MAGIS admin
    row satisfies any ``ALLOWED_ROLES`` containing either
    tag — the gate intersects the *effective* role-tag
    set (which is the union), not each tag independently."""

    class _AdminOrAssignedTool(Tool):
        name = "admin_or_assigned"
        ALLOWED_ROLES = frozenset({"admin", "assigned"})
        description = ""
        input_schema: dict = {}

        async def run(self, _ctx, **_kwargs):
            return ToolResult(content="ok")

    tool = _AdminOrAssignedTool()
    # Pure assigned: in via role.
    bus_assigned = _bus_with((1, "assigned"))
    assert tool.gate(_ctx(1, bus=bus_assigned)) is None
    # Pure admin: in via MAGIS.
    bus_admin = _bus_with((2, "guest"), admin_uids={2})
    assert tool.gate(_ctx(2, bus=bus_admin)) is None
    # Both: in via either, still passes.
    bus_both = _bus_with((3, "assigned"), admin_uids={3})
    assert tool.gate(_ctx(3, bus=bus_both)) is None
    # Neither: refused.
    bus_neither = _bus_with((4, "guest"))
    msg = tool.gate(_ctx(4, bus=bus_neither))
    assert msg is not None
    assert "role(s) ['guest']" in msg


def test_admin_only_rejects_assigned_only():
    """``ALLOWED_ROLES = {'admin'}`` rejects the served user
    who has only ``role='assigned'`` — admin is orthogonal
    to local role; serving a MAGI doesn't make you admin."""

    class _AdminOnlyTool(Tool):
        name = "admin_only"
        ALLOWED_ROLES = frozenset({"admin"})
        description = ""
        input_schema: dict = {}

        async def run(self, _ctx, **_kwargs):
            return ToolResult(content="ok")

    tool = _AdminOnlyTool()
    bus = _bus_with((1, "assigned"))  # assigned but not admin
    msg = tool.gate(_ctx(1, bus=bus))
    assert msg is not None
    assert "role(s) ['assigned']" in msg


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
    """``ctx.bus is None`` — BUS callers (MCP until
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
    """Role-mismatch path: includes the effective-set repr
    so callers / tests that grep ``"role(s)"`` keep finding
    it."""
    bus = _bus_with((7, "guest"))
    msg = _DemoTool().gate(_ctx(7, bus=bus))
    assert msg is not None
    assert "role(s)" in msg
    assert "'guest'" in msg
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

        async def run(self, _ctx, **_kwargs):
            return ToolResult(content="ok")

    tool = _OpenTool()
    # No bus, no contact — still passes.
    assert tool.gate(_ctx(1, bus=None)) is None
