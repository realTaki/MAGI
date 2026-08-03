
"""Persistent, workspace-free state for the singleton WebUI control plane."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive


class ControlSetting(Base):
    """Small control-plane KV store, deliberately separate from MAGI settings."""

    __tablename__ = "control_settings"

    key: Mapped[str] = mapped_column(String(191), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class ControlOperator(Base):
    """An administrator of the browser control plane, not a MAGI-local Contact."""

    __tablename__ = "control_operators"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )
