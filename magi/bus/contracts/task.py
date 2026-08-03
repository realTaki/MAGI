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
class TaskFullView:
    """Operator-facing row render: every column surfaced in the WebUI."""

    id: str
    name: str
    prompt: str
    cron: str
    run_at: str | None
    delivery_to: str | None
    tz: str
    target_channel: str
    uid: int
    enabled: bool
    consecutive_failures: int
    last_run_at: str | None
    last_status: str | None
    last_error: str | None
    created_at: str
    updated_at: str
    session_id: str | None
    preset_id: str | None
    preset_key: str | None


@dataclass(frozen=True, slots=True)
class TaskRunView:
    """Operator-facing run row (history pane)."""

    id: str
    task_id: str
    session_id: str | None
    trigger: str
    started_at: str
    finished_at: str | None
    latency_ms: int | None
    status: str
    error: str | None
    reply_excerpt: str | None
    input_tokens: int
    output_tokens: int


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