"""bus Base — SQLAlchemy declarative base.

bus 数据访问层：
- 自己的 ``DeclarativeBase``（独立 ``MetaData``）
- 自己的 ``EngineFactory``
- 自己的 ORM 类（inline 在每个 Book/Guild 文件里）
- 自己的 ``__tablename__``（如 ``chat_conversations``、``memory_entries`` 等）
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum as PyEnum

from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase


def utcnow_naive() -> datetime:
    """Return the current UTC time as a **naive** datetime.

    Used by every ORM ``default=`` / ``onupdate=`` in bus that
    stamps a row's ``created_at`` / ``updated_at``.  Returns a
    naive-UTC instant (DB column shape is ``DateTime`` with no tz).
    """
    return datetime.now(UTC).replace(tzinfo=None)


def enum_column(
    enum_cls: type[PyEnum],
    *,
    name: str | None = None,
    length: int = 24,
) -> SAEnum:
    """SAEnum 列工厂：PG native ENUM + SQLite CHECK + 老数据兼容。

    所有 enum 列都走这一份配置——避免每个文件 copy-paste 一份 SAEnum
    样板（``values_callable`` / ``create_constraint`` / ``length`` /
    ``native_enum``），也让 schema 演进（alembic migration）和 ORM 声明
    永远共享同一份真源。

    ``values_callable`` 把存储 / CHECK / CREATE TYPE 标签锁定到
    ``enum.value``（如 ``"started"``），不锁到 ``enum.name``（如
    ``"STARTED"``）——后者会让 SA 在所有现有行上做隐式重命名。

    ``name`` 默认用 ``enum_cls.__name__.lower()``，与 SQLAlchemy 默认
    行为一致。需要与 alembic migration 对齐固定名字时显式传（如
    :class:`magi.bus.guild.base.JobStatus` 的 ``"job_status"``）。

    ``length`` 默认 24 匹配老 ``VARCHAR(24)`` 列宽。SQLite 上控制
    VARCHAR(N)，PG native enum 不读 length（由成员字符串长度决定）。

    PG 走 ``CREATE TYPE <name>`` + 列引用；SQLite 无原生 ENUM，SA 自动
    fall back 到 ``VARCHAR(length)`` + ``CHECK (col IN (...))``。
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        length=length,
        create_constraint=True,
        values_callable=lambda cls: [m.value for m in cls],
    )


class Base(DeclarativeBase):
    """The single declarative base for every bus ORM table.

    Several bus ORM classes legitimately share ``__tablename__``
    (e.g. a ``library.local.*Book`` and its sibling
    ``guild.*Board``) because they describe the same SQLite table
    from two angles: the Book layer is CRUD; the Board layer is
    fire-and-forget. Whichever module is imported first wins the
    Table registration; every later module that declares an ORM
    with the same ``__tablename__`` must opt in with
    ``__table_args__ = {"extend_existing": True}`` — otherwise
    SQLAlchemy refuses the second registration.
    """
