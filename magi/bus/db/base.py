"""bus Base — SQLAlchemy declarative base.

bus 数据访问层：
- 自己的 ``DeclarativeBase``（独立 ``MetaData``）
- 自己的 ``EngineFactory``
- 自己的 ORM 类（inline 在每个 Book/Guild 文件里）
- 自己的 ``__tablename__``（如 ``chat_sessions``、``memory_entries`` 等）
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utcnow_naive() -> datetime:
    """Return the current UTC time as a **naive** datetime.

    Used by every ORM ``default=`` / ``onupdate=`` in bus that
    stamps a row's ``created_at`` / ``updated_at``.  Returns a
    naive-UTC instant (DB column shape is ``DateTime`` with no tz).
    """
    return datetime.now(UTC).replace(tzinfo=None)


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
