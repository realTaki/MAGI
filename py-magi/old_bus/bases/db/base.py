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
) -> SAEnum:
    """SAEnum 列工厂：PG native ENUM + SQLite CHECK + 值类型往返。

    所有 enum 列都走这一份配置——避免每个文件 copy-paste 一份 SAEnum
    样板（``values_callable`` / ``create_constraint`` / ``native_enum``），
    也让 schema 演进（alembic migration）和 ORM 声明永远共享同一份真源。

    ``values_callable`` 把存储 / CHECK / CREATE TYPE 标签锁定到
    ``enum.value``（如 ``"started"``），不锁到 ``enum.name``（如
    ``"STARTED"``）——后者会让 SA 在所有现有行上做隐式重命名。

    ``name`` 默认 ``None``，交由 SQLAlchemy 取 ``enum_cls.__name__`` 作为
    PG 的 ``CREATE TYPE`` 名。需要可读的 snake_case 名（如
    :class:`bus.bases.job.JobStatus` 的 ``"job_status"``）时显式传。

    列类型读回时自动还原成枚举成员（``row.status == JobStatus.PENDING``
    为真），Book 层无需再手动 ``_coerce_*``。PG 走原生 ``CREATE TYPE``；
    SQLite 无原生 ENUM，SA 自动 fall back 到 ``VARCHAR`` +
    ``CHECK (col IN (...))``，长度取成员 ``.value`` 最长者，无需手写。
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
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
