"""``schedule_task`` — create or update a cron Task.

Jobs:
  GetConversationJob — the fire target must exist.
  SetTaskJob — upsert by unique name (conversation_id sticks on update).
"""

from __future__ import annotations

from typing import Any

from bus import GetConversationJob, SetTaskJob
from tools.BaseTool import BaseTool, ToolResult

_PUBLISHER = "tools"
_FREQUENCIES = ("hourly", "daily", "weekly", "monthly")


class ScheduleTaskTool(BaseTool):
    """Create or update a cron task that fires into a conversation."""

    name = "schedule_task"
    description = (
        "Create or update a recurring task. Each fire becomes a ChatNotify "
        "in the given conversation. Same name updates the existing task. "
        "Inputs: name (unique, ≤120 chars), prompt, frequency "
        "('hourly' / 'daily' / 'weekly' / 'monthly'), hour (0..23, ignored "
        "for hourly), minute (0..59), day_of_week (0..6 Mon=0, weekly), "
        "day_of_month (1..31, monthly), conversation_id."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short label, ≤120 chars. Same name updates the existing task.",
            },
            "prompt": {
                "type": "string",
                "description": "Instruction to run each time the task fires.",
            },
            "frequency": {
                "type": "string",
                "enum": list(_FREQUENCIES),
                "description": "Preset cadence translated into a 5-field cron.",
            },
            "hour": {
                "type": "integer",
                "minimum": 0,
                "maximum": 23,
                "default": 0,
                "description": "Hour of day. Ignored when frequency='hourly'.",
            },
            "minute": {
                "type": "integer",
                "minimum": 0,
                "maximum": 59,
                "default": 0,
                "description": "Minute of hour. For hourly: fire at this minute past every hour.",
            },
            "day_of_week": {
                "type": "integer",
                "minimum": 0,
                "maximum": 6,
                "description": "Weekly only. 0=Mon … 6=Sun.",
            },
            "day_of_month": {
                "type": "integer",
                "minimum": 1,
                "maximum": 31,
                "description": "Monthly only. 1..31.",
            },
            "conversation_id": {
                "type": "integer",
                "description": "Conversation that receives each fire.",
            },
        },
        "required": ["name", "prompt", "frequency", "conversation_id"],
    }

    @BaseTool.require_bus
    async def run(self, **kwargs: Any) -> ToolResult:
        name = kwargs.get("name")
        prompt = kwargs.get("prompt")
        frequency = kwargs.get("frequency")
        if not isinstance(name, str) or not name.strip():
            return ToolResult.err("name is required")
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult.err("prompt is required")
        if frequency not in _FREQUENCIES:
            return ToolResult.err("frequency must be hourly, daily, weekly, or monthly")
        try:
            conversation_id = int(kwargs.get("conversation_id"))
        except (TypeError, ValueError):
            return ToolResult.err("conversation_id is required")
        if conversation_id <= 0:
            return ToolResult.err("conversation_id is required")
        cron = _preset_to_cron(
            frequency,
            hour=int(kwargs.get("hour") or 0),
            minute=int(kwargs.get("minute") or 0),
            day_of_week=kwargs.get("day_of_week"),
            day_of_month=kwargs.get("day_of_month"),
        )
        if cron is None:
            if frequency == "weekly":
                return ToolResult.err("day_of_week is required for weekly tasks")
            return ToolResult.err("day_of_month is required for monthly tasks")
        found = await self.publish(
            GetConversationJob(publisher=_PUBLISHER, conversation_id=conversation_id)
        )
        if found is None:
            return ToolResult.err("conversation book is not available")
        if found.conversation is None:
            return ToolResult.err(f"unknown conversation {conversation_id}")
        result = await self.publish(
            SetTaskJob(
                publisher=_PUBLISHER,
                name=name.strip(),
                prompt=prompt.strip(),
                cron=cron,
                conversation_id=conversation_id,
            )
        )
        if result is None:
            return ToolResult.err("task book is not available")
        return ToolResult.ok(
            {
                "task_id": result.task_id,
                "name": name.strip(),
                "cron": cron,
                "conversation_id": conversation_id,
            }
        )


def _preset_to_cron(
    frequency: str,
    *,
    hour: int,
    minute: int,
    day_of_week: object,
    day_of_month: object,
) -> str | None:
    minute = max(0, min(int(minute), 59))
    hour = max(0, min(int(hour), 23))
    if frequency == "hourly":
        return f"{minute} * * * *"
    if frequency == "daily":
        return f"{minute} {hour} * * *"
    if frequency == "weekly":
        if not isinstance(day_of_week, int) or not 0 <= day_of_week <= 6:
            return None
        cron_dow = 0 if day_of_week == 6 else day_of_week + 1
        return f"{minute} {hour} * * {cron_dow}"
    if frequency == "monthly":
        if not isinstance(day_of_month, int) or not 1 <= day_of_month <= 31:
            return None
        return f"{minute} {hour} {day_of_month} * *"
    return None


__all__ = ["ScheduleTaskTool"]
