"""SQLAlchemy declarative base + 时间工具。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utcnow_naive() -> datetime:
    """返回当前 UTC 时间的 naive datetime（无时区）。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """所有 ORM 模型共享的基类。"""
