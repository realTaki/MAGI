"""Immutable task facts exposed to channel schedulers and WebUI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskScheduleView:
    id: str
    enabled: bool
    cron: str
    run_at: str | None
