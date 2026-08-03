"""Immutable task facts exposed to channel schedulers and WebUI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskScheduleView:
    id: str
    enabled: bool
    cron: str
    run_at: str | None


@dataclass(frozen=True, slots=True)
class TaskExecution:
    """Committed task-run context consumed by the task channel worker."""

    task_id: str
    run_id: str
    session_id: str
    uid: int
    caller_role: str | None
    task_name: str
    prompt: str
    cron: str
    run_at: str | None
    tz: str
    target_channel: str
    delivery_to: str | None


@dataclass(frozen=True, slots=True)
class TaskPresetView:
    id: str
    key: str
    name: str
    description: str
    prompt: str
    frequency: str
    hour: int
    minute: int
    day_of_week: int | None
    day_of_month: int | None
    run_at: str | None
    target_channel: str
    enabled: bool
    created_at: str
    updated_at: str
