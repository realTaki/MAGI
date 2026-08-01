"""Smoke tests for the v0 gmail / calendar stub tools.

Three surfaces pinned:

  1. **Shape** — both tools return a JSON object with
     ``_stub: True`` plus the canonical keys
     (emails / meetings). v0 callers can rely on the field
     names; the OAuth-backed replacements land in C5.
  2. **ALLOWED_ROLES** — ``frozenset()`` makes both tools
     visible to every role (operator, assigned, contact,
     guest). The OAuth replacements will tighten to
     ``{"assigned"}``.
  3. **Window** — the ``hours`` and ``days`` knobs thread
     through to the returned payload.
"""

from __future__ import annotations

import json

import pytest


def _ctx():
    """Minimal ToolContext — uid / state_dir / channel don't
    matter for the stub tools."""
    from types import SimpleNamespace
    return SimpleNamespace(
        state_dir="/tmp/whatever",
        workspace="/tmp/whatever",
        uid=1,
        channel="webui",
        session_id="01ABCDEFGHJKMNPQRSTVWXYZAB",
        caller_role="assigned",
        admin=True,
    )


# -- read_recent_emails ------------------------------------------------------


@pytest.mark.asyncio
async def test_read_recent_emails_default_window():
    from magi.agent.tools.services_stub import ReadRecentEmailsTool
    tool = ReadRecentEmailsTool()
    res = await tool.run(_ctx())
    assert res.is_error is False
    payload = json.loads(res.content)
    assert payload["_stub"] is True
    assert payload["hours"] == 24
    assert isinstance(payload["emails"], list)
    assert len(payload["emails"]) >= 1
    # Every email has the canonical shape.
    for e in payload["emails"]:
        assert {"subject", "from", "received_at", "snippet"} <= set(e.keys())


@pytest.mark.asyncio
async def test_read_recent_emails_hours_param_threads_through():
    from magi.agent.tools.services_stub import ReadRecentEmailsTool
    tool = ReadRecentEmailsTool()
    res = await tool.run(_ctx(), hours=72)
    payload = json.loads(res.content)
    assert payload["hours"] == 72


@pytest.mark.asyncio
async def test_read_recent_emails_visible_to_guest():
    """``ALLOWED_ROLES = frozenset()`` makes the stub tools
    visible to every role, including guest."""
    from magi.agent.tools.base import Tool
    from magi.agent.tools.services_stub import ReadRecentEmailsTool
    assert ReadRecentEmailsTool().is_allowed_for_role("guest") is True
    assert ReadRecentEmailsTool().is_allowed_for_role(None) is True


# -- read_upcoming_meetings --------------------------------------------------


@pytest.mark.asyncio
async def test_read_upcoming_meetings_default_window():
    from magi.agent.tools.services_stub import ReadUpcomingMeetingsTool
    tool = ReadUpcomingMeetingsTool()
    res = await tool.run(_ctx())
    assert res.is_error is False
    payload = json.loads(res.content)
    assert payload["_stub"] is True
    assert payload["days"] == 1
    assert isinstance(payload["meetings"], list)
    assert len(payload["meetings"]) >= 1
    for m in payload["meetings"]:
        assert {"title", "start", "end", "attendees"} <= set(m.keys())


@pytest.mark.asyncio
async def test_read_upcoming_meetings_days_param_threads_through():
    from magi.agent.tools.services_stub import ReadUpcomingMeetingsTool
    tool = ReadUpcomingMeetingsTool()
    res = await tool.run(_ctx(), days=7)
    payload = json.loads(res.content)
    assert payload["days"] == 7


@pytest.mark.asyncio
async def test_read_upcoming_meetings_visible_to_guest():
    from magi.agent.tools.services_stub import ReadUpcomingMeetingsTool
    assert ReadUpcomingMeetingsTool().is_allowed_for_role("guest") is True


# -- registered in the tool registry -----------------------------------------


def test_stub_tools_are_in_the_registry():
    """``tools/registry.py`` registers both stubs alongside the
    other built-in tools."""
    from magi.agent.tools.services_stub import (
        ReadRecentEmailsTool,
        ReadUpcomingMeetingsTool,
    )
    from magi.agent.tools.registry import get_tools, reset_cache

    # ``reset_cache`` is idempotent and guards against stale
    # fixtures from earlier tests.
    reset_cache()
    tools = get_tools(caller_role="guest")
    names = {t.name for t in tools}
    assert "read_recent_emails" in names
    assert "read_upcoming_meetings" in names