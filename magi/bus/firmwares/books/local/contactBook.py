"""ContactBook + ContactNoteBook — local people, admin projections, and notes.

Two tables:
- ``contacts``       — people local to this MAGI, including MAGIS-admin projections
- ``contact_notes``  — one row per fact (kind='permanent') or daily log (kind='daily')

Schema for ``contacts`` + ``contact_notes`` tables.

Book contract. Both books own **data access** — callers
(LLM-driven tools, channel API routes) interact through
frozen DTOs (``:meth:`to_dict`` for JSON serialisation);
SQLAlchemy rows stay inside the short repository
transaction.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Text,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin
from magi.bus.bases.db.base import enum_column, utcnow_naive


class NoteKind(StrEnum):
    """Note-kind discriminator stored on ``ContactNote.kind``.

    ``PERMANENT`` is the long-lived-facts default — every note
    inserted via :meth:`ContactNoteBook.add` lands here unless
    the caller opts in. ``DAILY`` is one row per
    ``(contact_id, note_date)`` stamped at UTC midnight; only
    :meth:`ContactNoteBook.upsert_daily_note` writes it.

    ``StrEnum`` rather than bare constants so typos are caught
    at lookup time instead of silently comparing False: every
    member is still a ``str`` (``NoteKind.DAILY == "daily"``),
    so ORM columns, ``asdict`` serialisation and existing rows
    keep working unchanged. Mirrors
    :class:`magi.bus.firmwares.books.local.actionItemBook.ActionSource`.
    """

    PERMANENT = "permanent"
    DAILY = "daily"


class Role(StrEnum):
    """MAGI-local role tag stored on ``Contact.role``.

    ``ASSIGNED`` means the contact owns this MAGI (the chat
    channel binds every inbound message to exactly one such
    contact). ``GUEST`` is every other locally-known contact —
    reached via shared conversation, manual lookup, or just
    having a Telegram chat id captured before a bind.

    ``StrEnum`` rather than bare string constants so typos are
    caught at lookup time instead of silently comparing False:
    every member is still a ``str`` (``Role.GUEST == "guest"``),
    so ORM columns, ``asdict`` serialisation, ``==`` /
    ``!=`` against string literals and existing rows keep
    working unchanged. Admin authority is **not** here — it's a
    MAGIS-level concept and lives in
    :class:`~magi.bus.firmwares.books.magis.magisBook.MagisAdminBook`.
    """

    ASSIGNED = "assigned"
    GUEST = "guest"


# -- public dataclasses --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Contact(BaseRecord):
    """Per-MAGI operator record.

    ``role`` is the MAGI-local role tag (``assigned`` /
    ``guest`` / ``contact``). Admin is **not** a column
    here — it's a MAGIS-level concept and lives in
    :class:`~magi.bus.firmwares.books.magis.magisBook.MagisAdminBook`
    (``magis_admins`` table). A user can be ``assigned`` in
    this MAGI **and** admin in any MAGIS — the two flags
    are orthogonal. The agent catalog combines both when
    filtering the tool menu.
    """

    name: str  # 联系人唯一名
    display_name: str | None = None
    role: Role = Role.GUEST  # MAGI 本地角色（assigned/guest）
    tgid: int | None = None  # 绑定的 Telegram chat id（本地用户身份）
    # Nullable projection link to the MAGIS-shared operator identity.  It is
    # deliberately not a foreign key because the two stores are independent.
    magis_admin_id: int | None = None
    last_seen_at: datetime | None = None  # 最近活跃时间

@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ContactNote(BaseRecord):
    contact_id: int  # 所属联系人 ID
    note: str  # 笔记正文
    kind: NoteKind = NoteKind.PERMANENT  # 笔记类型（permanent/daily）
    note_date: datetime | None = None  # 日记所属日期

# -- internal ORM --------------------------------------------------------


class _ContactRow(BaseRecordMixin):
    __tablename__ = "contacts"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[Role] = mapped_column(enum_column(Role), nullable=False, default=Role.GUEST)
    tgid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    magis_admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class _ContactNoteRow(BaseRecordMixin):
    __tablename__ = "contact_notes"

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[NoteKind] = mapped_column(enum_column(NoteKind), nullable=False, default=NoteKind.PERMANENT)
    note_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# -- Books ---------------------------------------------------------------


class ContactBook(BaseBook[_ContactRow, Contact]):
    model_cls = _ContactRow
    record_cls = Contact

    def get_by_telegram(self, *, tgid: int) -> Contact | None:
        with self._session() as s:
            row = s.scalar(select(_ContactRow).where(_ContactRow.tgid == tgid))
            return self.record_cls.from_row(row) if row else None

    def get_by_magis_admin_id(self, *, magis_admin_id: int) -> Contact | None:
        """Return this runtime's local projection of one MAGIS admin."""
        with self._session() as s:
            row = s.scalar(
                select(_ContactRow).where(_ContactRow.magis_admin_id == magis_admin_id)
            )
            return self.record_cls.from_row(row) if row else None

    def ensure_magis_admin_projection(
        self, *, magis_admin_id: int, display_name: str | None
    ) -> Contact:
        """Return this MAGI's Contact projection for a shared admin identity.

        The deterministic internal name never claims an unrelated local user
        named ``admin``.  ``magis_admin_id`` is unique, so concurrent callers
        converge on the same projection rather than creating authority data
        in the local store.
        """
        with self._session() as s:
            existing = s.scalar(
                select(_ContactRow).where(_ContactRow.magis_admin_id == magis_admin_id)
            )
            if existing is not None:
                return self.record_cls.from_row(existing)
            base_name = f"magis-admin-{magis_admin_id}"
            candidate = base_name
            suffix = 1
            while s.scalar(select(_ContactRow.id).where(_ContactRow.name == candidate)) is not None:
                candidate = f"{base_name}-projection-{suffix}"
                suffix += 1
            row = _ContactRow(
                name=candidate,
                display_name=display_name,
                role=Role.GUEST,
                magis_admin_id=magis_admin_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return self.record_cls.from_row(row)

    def touch(self, *, contact_id: int | None) -> None:
        """Stamp ``last_seen_at`` for a contact.

        Cheap, idempotent activity signal — called by the
        channel→agent publish path (:meth:`magi.bus.firmwares.jobs.chatNotifyJob.chatNotifyBoard.publish`)
        so :meth:`search`'s recency ordering reflects real
        inbound traffic. A no-op when ``contact_id`` is
        ``None`` (e.g. a cron task without a bound contact)
        or when no row matches the id, so callers don't have
        to pre-check before publishing a turn.
        """
        if contact_id is None:
            return
        with self._session() as s:
            s.execute(
                update(_ContactRow)
                .where(_ContactRow.id == contact_id)
                .values(last_seen_at=utcnow_naive())
            )
            s.commit()

    def _record_to_row_values(self, record: Contact, session) -> dict:
        values = super()._record_to_row_values(record, session)
        if record.tgid is not None:
            bound = session.scalar(
                select(_ContactRow).where(_ContactRow.tgid == record.tgid, _ContactRow.id != record.id)
            )
            if bound is not None:
                raise ValueError("tgid already bound")
        return values

    def list_all(self) -> list[Contact]:
        with self._session() as s:
            rows = s.scalars(select(_ContactRow).order_by(_ContactRow.id)).all()
            return [self.record_cls.from_row(r) for r in rows]

    def set_tgid(
        self,
        *,
        contact_id: int,
        tgid: int | None,
    ) -> Contact | None:
        """[claude, 2026-08-08] Bind / unbind a Telegram chat id on a contact.

        ``tgid=None`` clears the binding. Returns the updated
        :class:`Contact` or ``None`` if no row matches.

        Required by ``magi/channels/telegram/adapter.py`` and
        ``magi/channels/api/tg_bindings.py`` through their explicit BUS
        dependency.
        """
        record = self.get(contact_id)
        if record is None:
            return None
        candidate = dataclasses.replace(record, tgid=tgid)
        self.update(candidate)
        return self.get(contact_id)

    def search(self, *, query: str, limit: int = 20) -> list[Contact]:
        """Case-insensitive substring search across name and notes.

        Two-pass: rows whose ``name`` matches come first;
        then rows whose ``contact_notes.note`` matches are
        appended (de-duplicated by id). The result is
        ordered by ``last_seen_at`` descending so recent
        activity floats to the top — same shape the old
        bus's ``ContactsService.search`` exposed.
        ``limit`` is the cap on the **returned** list
        (after both passes merge), matching the old
        behaviour.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        pattern = f"%{query.strip()}%"
        with self._session() as s:
            name_rows = list(
                s.scalars(
                    select(_ContactRow)
                    .where(_ContactRow.name.ilike(pattern))
                    .order_by(_ContactRow.id)
                )
            )
            seen = {row.id for row in name_rows}
            matched_ids = set(
                s.scalars(
                    select(_ContactNoteRow.contact_id)
                    .where(_ContactNoteRow.note.ilike(pattern))
                    .distinct()
                )
            )
            for row in s.scalars(select(_ContactRow).where(_ContactRow.id.in_(matched_ids))).all():
                if row.id not in seen:
                    name_rows.append(row)
                    seen.add(row.id)
            name_rows.sort(
                key=lambda row: row.last_seen_at or datetime.min,
                reverse=True,
            )
            return [self.record_cls.from_row(r) for r in name_rows[:limit]]

class ContactNoteBook(BaseBook[_ContactNoteRow, ContactNote]):
    model_cls = _ContactNoteRow
    record_cls = ContactNote

    def list_for_contact(self, *, contact_id: int) -> list[ContactNote]:
        with self._session() as s:
            rows = s.scalars(
                select(_ContactNoteRow)
                .where(_ContactNoteRow.contact_id == contact_id)
                .order_by(_ContactNoteRow.id.desc())
            ).all()
            return [self.record_cls.from_row(r) for r in rows]

    def read_daily_note(self, *, contact_id: int) -> ContactNote | None:
        """Return today's daily-note row for *contact_id*, or ``None``."""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with self._session() as s:
            row = s.scalar(
                select(_ContactNoteRow).where(
                    _ContactNoteRow.contact_id == contact_id,
                    _ContactNoteRow.kind == NoteKind.DAILY,
                    _ContactNoteRow.note_date >= today,
                )
            )
            return self.record_cls.from_row(row) if row else None

    def upsert_daily_note(
        self,
        *,
        contact_id: int,
        body_delta: str,
        note_date: datetime | None = None,
    ) -> ContactNote:
        """Append a delta to today's daily note.

        One row per ``(contact_id, note_date)`` —
        ``kind=NoteKind.DAILY``. On a hit, the new line is
        appended with a ``"\\n"`` separator. On a
        miss, a fresh row is inserted.

        ``note_date`` defaults to today's UTC midnight —
        callers passing an explicit date are back-filling a
        missed day; the Book stamps it verbatim.

        Raises :class:`ValueError` if the parent contact id does
        not resolve — same as :meth:`add`.
        """
        content = body_delta
        if note_date is None:
            now = datetime.utcnow()
            note_date = datetime(now.year, now.month, now.day)
        with self._session() as s:
            row = s.scalar(
                select(_ContactNoteRow).where(
                    _ContactNoteRow.contact_id == contact_id,
                    _ContactNoteRow.kind == NoteKind.DAILY,
                    _ContactNoteRow.note_date == note_date,
                )
            )
            if row is None:
                row = _ContactNoteRow(
                    contact_id=contact_id,
                    note=content,
                    kind=NoteKind.DAILY,
                    note_date=note_date,
                )
                s.add(row)
            else:
                row.note = row.note + "\n" + content
            s.commit()
            s.refresh(row)
        return self.record_cls.from_row(row)


__all__ = [
    "Contact",
    "ContactNote",
    "ContactBook",
    "ContactNoteBook",
    "_ContactRow",
    "_ContactNoteRow",
    "NoteKind",
    "Role",
]
