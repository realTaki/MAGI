
"""Stub external-service tools (gmail, calendar).

v0 returns mock data so the morning / night report prompt
chain runs end-to-end without OAuth credentials. The real
Gmail / Calendar integrations land in C5 — when they ship,
this module is replaced one tool at a time; the shape of the
returned JSON is what the prompts already consume.

Why ``ALLOWED_ROLES = frozenset()`` (all roles see these):

  - The stub data is non-sensitive. A guest chatting with
    MAGI can mention "your fake mock email said X" without
    leaking anything real.
  - The OAuth-backed replacements will tighten to
    ``frozenset({"assigned"})`` once the data becomes real
    (a guest shouldn't see an operator's inbox).
  - Empty-allowed-roles is the same shape the rest of the
    system tools use for low-sensitivity utilities.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from magi.tools.base import Tool, ToolContext, ToolResult


logger = logging.getLogger("magi.tools.services_stub")


def _ok(payload: Any) -> ToolResult:
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    if len(body) > 4 * 1024:
        body = body[: 4 * 1024] + "\n…(truncated)"
    return ToolResult(content=body, is_error=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    """UTC midnight of today — used for stable mock timestamps."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


# -- read_recent_emails ------------------------------------------------------


class ReadRecentEmailsTool(Tool):
    """Return emails received in the last N hours (v0 stub).

    The mock JSON mirrors the shape a real Gmail adapter would
    return — ``subject``, ``from``, ``received_at``, ``snippet``.
    The morning report reads this verbatim and structures
    邮件高光 from the results.

    When the OAuth-backed Gmail integration ships (C5), the
    implementation behind this same ``run()`` swaps to a real
    ``gmail.list_messages(...)`` call. The schema and the
    tool name stay stable so the morning / night report
    prompts don't have to change.
    """

    name = "read_recent_emails"
    ALLOWED_ROLES = frozenset()
    description = (
        "Read emails received in the last N hours. Returns a "
        "list of {subject, from, received_at, snippet}. v0 "
        "returns placeholder data; a real Gmail adapter "
        "lands in C5."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": 168,
                "default": 24,
                "description": "Window in hours; default 24.",
            },
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        hours = int(kwargs.get("hours") or 24)
        now = datetime.now(timezone.utc)
        mock = [
            {
                "subject": "[样本邮件] Q3 财务报告确认",
                "from": "finance@example.com",
                "received_at": (now - timedelta(hours=2)).isoformat(),
                "snippet": "Q3 报告已审核,请查收附件 — sample stub data.",
            },
            {
                "subject": "[样本邮件] 明天 10 点客户会议提醒",
                "from": "calendar-noreply@example.com",
                "received_at": (now - timedelta(hours=6)).isoformat(),
                "snippet": "Acme Co. 周二上午 10 点的会议将在 15 分钟后开始 — sample.",
            },
            {
                "subject": "[样本邮件] 行动项:跟进 Lily 的报销",
                "from": "ops@example.com",
                "received_at": (now - timedelta(hours=11)).isoformat(),
                "snippet": "Lily 提交的差旅报销还差一张发票 — sample.",
            },
        ]
        return _ok({"hours": hours, "emails": mock, "_stub": True})


# -- read_upcoming_meetings --------------------------------------------------


class ReadUpcomingMeetingsTool(Tool):
    """Return meetings on the calendar for the next N days (v0 stub).

    The mock JSON mirrors what a real Google Calendar adapter
    would return — ``title``, ``start``, ``end``, ``attendees``.
    The morning report reads this verbatim to populate
    今日行程.
    """

    name = "read_upcoming_meetings"
    ALLOWED_ROLES = frozenset()
    description = (
        "Read meetings on the calendar for the next N days. "
        "Returns a list of {title, start, end, attendees}. "
        "v0 returns placeholder data; a real Calendar adapter "
        "lands in C5."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 14,
                "default": 1,
                "description": "Window in days; default 1.",
            },
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        days = int(kwargs.get("days") or 1)
        now = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        mock = [
            {
                "title": "[样本会议] 团队晨会",
                "start": (now + timedelta(hours=10)).isoformat(),
                "end": (now + timedelta(hours=10, minutes=30)).isoformat(),
                "attendees": ["alice", "bob", "carol"],
            },
            {
                "title": "[样本会议] Acme Co. 客户对接",
                "start": (now + timedelta(hours=14)).isoformat(),
                "end": (now + timedelta(hours=15)).isoformat(),
                "attendees": ["alice", "dan@acme.example"],
            },
            {
                "title": "[样本会议] 1:1 — Mark",
                "start": (now + timedelta(days=1, hours=9)).isoformat(),
                "end": (now + timedelta(days=1, hours=9, minutes=30)).isoformat(),
                "attendees": ["alice", "mark"],
            },
        ]
        return _ok({"days": days, "meetings": mock, "_stub": True})


__all__ = [
    "ReadRecentEmailsTool",
    "ReadUpcomingMeetingsTool",
]
