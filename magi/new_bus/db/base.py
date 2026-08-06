"""new_bus Base — 独立于旧 bus 的全新 SQLAlchemy declarative base.

new_bus 是完全独立的数据访问层：
- 自己的 ``DeclarativeBase``（独立 ``MetaData``）
- 自己的 ``EngineFactory``（不依赖 ``magi.bus.db.engine``）
- 自己的 ORM 类（inline 在每个 Book/Queue 文件里）
- 自己的 ``__tablename__``（与旧 bus 的表名保持兼容，但属于独立 metadata）

``__tablename__`` 与旧 bus **同名**（如 ``chat_sessions``、``llm_attempts``），
因为 new_bus 与旧 bus 写同一份 SQLite schema；两边在两个独立的
``MetaData`` 实例里各自注册同名 Table，SQLAlchemy 不会跨 metadata
冲突——这两个 Table 在物理上是同一张表（同一 SQLite file），但
SQLAlchemy 把它们视作逻辑独立的 schema。

如果 caller 想用 new_bus 完全替代旧 bus，可以：
1. 在独立的 SQLite 文件里跑（``EngineFactory("sqlite:////tmp/new.db")``）
2. 在同一 SQLite 文件里跑（两边都 ``create_all`` 一次；后续表已存在
   时是 no-op）
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utcnow_naive() -> datetime:
    """Return the current UTC time as a **naive** datetime.

    Used by every ORM ``default=`` / ``onupdate=`` in new_bus that
    stamps a row's ``created_at`` / ``updated_at``.  Returns a
    naive-UTC instant (DB column shape is ``DateTime`` with no tz).
    """
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """The single declarative base for every new_bus ORM table.

    Independent from ``magi.bus.db.base.Base`` — the new_bus has its
    own ``MetaData`` instance and its own Table registry.  Sharing
    a single SQLite file with the old bus is supported because the
    two metadatas never see each other's Tables.
    """
