"""BaseBook — 数据簿基类，自动映射 ORM → dataclass。

子类提供 model_cls / record_cls 两个类属性即可。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Self

from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from old_bus.bases.db.base import Base, utcnow_naive
from old_bus.bases.db.engine import EngineFactory


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BaseRecord:
    """Common JSON-safe fields for every persisted library DTO.

    ``id`` and audit timestamps are database-owned: Books stamp them at
    write time (see :meth:`BaseBook.add` / :meth:`BaseBook.update`) and
    :meth:`from_row` fills them from a persisted row.  Callers may supply
    them when constructing a DTO, but Books deliberately overwrite them —
    they are not part of the caller's data contract.  Time values remain
    ``datetime`` throughout the database, Book and API layers; presentation
    formatting belongs to the frontend.
    """

    id: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict:
        """Return the DTO's transport-ready field mapping.

        Values deliberately retain their native types, including ``datetime``;
        the API transport is responsible for JSON encoding and the frontend
        for presentation formatting.  A record with a genuinely different
        public projection may override this method locally.
        """

        return dataclasses.asdict(self)

    @classmethod
    def from_row(cls, row: BaseRecordMixin) -> Self:
        """Rebuild a Record from its persisted ORM row.

        Deliberately mechanical: every DTO field whose name matches a row
        attribute — including the database-owned ``id`` / ``created_at`` /
        ``updated_at`` — is copied verbatim.  No subclass customisation is
        expected; storage shape is kept aligned with the DTO (column names,
        SQLAlchemy ``JSON`` columns), so a read is always a faithful
        field-for-field projection of the row.
        """
        kwargs = {
            f.name: getattr(row, f.name)
            for f in dataclasses.fields(cls)
            if hasattr(row, f.name)
        }
        return cls(**kwargs)


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

    def _validate_add(self, record: RecordT) -> None:
        """Validate a new record before it is persisted.

        Subclasses own domain invariants and override this hook where needed.
        They must not open or commit a separate transaction.
        """

    def _record_to_row_values(self, record: RecordT, _session) -> dict:
        """Map an input DTO to ORM constructor / update values.

        The default derives the one-to-one column mapping from
        :meth:`BaseRecord.to_dict`, so a newly added DTO field is picked up
        automatically as long as a column with the same name exists.  Only
        ``id`` (the database-owned autoincrement primary key) is excluded —
        it must never reach the row constructor.  ``created_at`` /
        ``updated_at`` flow through so the DTO and the stored row share the
        same timestamps (Books stamp them before this hook runs).  Books with
        semantic references or encoded storage columns override the hook and
        may use the supplied session to resolve those references; such
        overrides must not call this default unless every DTO field maps to a
        real column.
        """

        values = record.to_dict()
        values.pop("id", None)  # DB autoincrement PK; never handed to the row constructor
        unmapped = [name for name in values if not hasattr(self.model_cls, name)]
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

        The DTO's database-owned fields are stamped defensively: ``id`` must
        be 0 (a caller-supplied value would be silently dropped otherwise),
        and a missing ``created_at`` is defaulted so validation and storage
        see a complete record.
        """

        if record.id != 0:
            raise ValueError("add() accepts only an unpersisted record (id must be 0)")
        now = utcnow_naive()
        prepared = dataclasses.replace(
            record,
            created_at=record.created_at or now,
            updated_at=now,
        )
        self._validate_add(prepared)
        with self._session() as session:
            row = self.model_cls(**self._record_to_row_values(prepared, session))
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
            return self.record_cls.from_row(row) if row is not None else None

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
            # Stamp ``updated_at`` and re-anchor ``created_at`` to the stored
            # value so a caller-constructed DTO cannot corrupt the audit trail.
            prepared = dataclasses.replace(
                record,
                created_at=row.created_at,
                updated_at=utcnow_naive(),
            )
            for name, value in self._record_to_row_values(prepared, session).items():
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
