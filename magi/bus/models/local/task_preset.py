"""Persistence model for system-proactive task templates.

``TaskPreset`` describes a system policy that may seed an individual MAGI's
ordinary scheduled tasks.  The resulting ``Task`` rows are BUS-owned local
models and are executed by the task channel.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.db import Base


class TaskPreset(Base):
    """A reusable proactive template snapshotted into a user's task row."""

    __tablename__ = "task_presets"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    hour: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_channel: Mapped[str] = mapped_column(
        "channel", String(16), nullable=False, default="webui"
    )
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
