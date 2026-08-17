"""BaseBook — 数据簿基类，自动映射 ORM → dataclass。

子类提供 model_cls / record_cls 两个类属性即可。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.db.engine import EngineFactory


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BaseRecord:
    """Common JSON-safe fields for every persisted library DTO.

    ``id`` and audit timestamps are database-owned. Callers cannot supply
    them when constructing a DTO; Books fill them from a persisted row. Time
    values deliberately remain ``datetime`` throughout the database, Book and
    API layers; presentation formatting belongs to the frontend.
    """

    id: int = dataclasses.field(default=0, init=False)
    created_at: datetime | None = dataclasses.field(default=None, init=False)
    updated_at: datetime | None = dataclasses.field(default=None, init=False)

    def to_dict(self) -> dict:
        """Return the DTO's transport-ready field mapping.

        Values deliberately retain their native types, including ``datetime``;
        the API transport is responsible for JSON encoding and the frontend
        for presentation formatting.  A record with a genuinely different
        public projection may override this method locally.
        """

        return dataclasses.asdict(self)

    def with_changes[Self: "BaseRecord"](self: Self, /, **changes) -> Self:
        """Return a validated replacement while retaining database-owned fields.

        ``dataclasses.replace`` intentionally omits ``init=False`` fields;
        that is correct for ordinary dataclasses, but would turn a persisted
        Record back into an unpersisted one by dropping its ``id``.  This is
        the sole supported way to prepare a Record for :meth:`BaseBook.update`.
        """
        replacement = dataclasses.replace(self, **changes)
        for name in ("id", "created_at", "updated_at"):
            object.__setattr__(replacement, name, getattr(self, name))
        return replacement


class BaseRecordMixin(Base):
    """The single ORM record shape shared by all library tables."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class BaseBook[RowT: BaseRecordMixin, RecordT: BaseRecord]:
    """子类设置 model_cls / record_cls，自动处理 Session 和映射。"""

    model_cls: type[RowT]
    record_cls: type[RecordT]

    def __init__(self, factory: EngineFactory):
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def _row_to_dto(self, row: RowT) -> RecordT:
        init_kwargs: dict = {}
        database_values: dict = {}
        for f in dataclasses.fields(self.record_cls):
            if hasattr(row, f.name):
                val = getattr(row, f.name)
                if f.init:
                    init_kwargs[f.name] = val
                else:
                    database_values[f.name] = val
        dto = self.record_cls(**init_kwargs)
        for name, value in database_values.items():
            object.__setattr__(dto, name, value)
        return dto

    def _validate_add(self, record: RecordT) -> None:
        """Validate a new record before it is persisted.

        Subclasses own domain invariants and override this hook where needed.
        They must not open or commit a separate transaction.
        """

    def _record_to_row_values(self, record: RecordT, _session) -> dict:
        """Map an input DTO to ORM constructor values.

        The default applies to Books whose DTO field names map one-to-one to
        model columns. Books with semantic references or encoded storage
        columns override the hook and may use the supplied session to resolve
        those references.
        """

        values: dict = {}
        unmapped: list[str] = []
        for field in dataclasses.fields(record):
            if not field.init:
                continue
            if not hasattr(self.model_cls, field.name):
                unmapped.append(field.name)
                continue
            values[field.name] = getattr(record, field.name)
        if unmapped:
            raise TypeError(
                f"{type(self).__name__} must map DTO-only fields explicitly: "
                f"{', '.join(unmapped)}"
            )
        return values

    def add(self, record: RecordT) -> int:
        """Persist a new DTO and return its database-generated row ID.

        ``add`` is deliberately a command: callers supply the complete
        unpersisted record and receive only the generated primary key. Use
        ``get`` / ``list`` for DTO reads.
        """

        if record.id != 0:
            raise ValueError("add() accepts only an unpersisted record (id must be 0)")
        self._validate_add(record)
        with self._session() as session:
            row = self.model_cls(**self._record_to_row_values(record, session))
            session.add(row)
            session.commit()
            return row.id

    def get(self, record_id: int) -> RecordT | None:
        """Read one DTO by its database-local primary key.

        Business-key lookups belong in explicitly named methods such as
        ``get_by_conversation_id``. This keeps the unqualified ``get``
        contract identical for every database-backed Book.
        """

        with self._session() as session:
            row = session.get(self.model_cls, record_id)
            return self._row_to_dto(row) if row is not None else None

    def update(self, record: RecordT) -> bool:
        """Replace the persisted row identified by ``record.id``.

        ``Record`` is deliberately a complete immutable value, rather than a
        bag of optional PATCH fields.  Callers that start with a partial input
        read the DTO, use :meth:`BaseRecord.with_changes`, then pass that complete
        value here.  ``True`` means a row was replaced; ``False`` means its
        database-local ID no longer exists.

        ``_validate_add`` runs *before* the session opens — same shape as
        :meth:`add` — so subclasses that open their own session in the
        validator (e.g. :class:`ConversationBook._validate_add` reading
        ``settings_book.channel_options()``) don't trigger a nested
        transaction.
        """
        if record.id <= 0:
            raise ValueError("update() requires a persisted record (id must be positive)")
        self._validate_add(record)
        with self._session() as session:
            row = session.get(self.model_cls, record.id)
            if row is None:
                return False
            for name, value in self._record_to_row_values(record, session).items():
                setattr(row, name, value)
            session.commit()
            return True

    def delete(self, record_id: int) -> bool:
        """Delete one row by its database-local ID, idempotently."""
        with self._session() as session:
            row = session.get(self.model_cls, record_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
