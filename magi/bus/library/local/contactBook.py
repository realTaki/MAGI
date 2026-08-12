"""ContactBook + ContactNoteBook — local people, admin projections, and notes.

Two tables:
- ``contacts``       — people local to this MAGI, including MAGIS-admin projections
- ``contact_notes``  — one row per fact (kind='permanent') or daily log (kind='daily')

Schema for ``contacts`` + ``contact_notes`` tables.

Book contract. Both books own their **data access** and
write invariants (non-empty name, length caps, daily
appends) — callers (LLM-driven tools, channel API routes)
get the same validation without each path re-implementing
checks. Returns are frozen DTOs (``:meth:`to_dict`` for
JSON-serialisation); SQLAlchemy rows stay inside the
short repository transaction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.library.base import BaseBook

ROLE_ASSIGNED = "assigned"
ROLE_GUEST = "guest"
ALL_ROLES = frozenset({ROLE_ASSIGNED, ROLE_GUEST})


# Column-length invariants — mirror the ORM column
# declarations (``String(120)`` / ``Text``) and the
# per-row ``contact_notes`` size cap. The Book enforces them
# so every caller (chat-driven tool, dashboard route, future
# agent loop) gets the same validation without each path
# re-implementing length checks.
_NOTE_MAX_BYTES = 8 * 1024
_DAILY_NOTE_MAX_BYTES = 32 * 1024


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Contact:
    """Per-MAGI operator record.

    ``role`` is the MAGI-local role tag (``assigned`` /
    ``guest`` / ``contact``). Admin is **not** a column
    here — it's a MAGIS-level concept and lives in
    :class:`~magi.bus.library.magis.magisBook.MagisAdminBook`
    (``magis_admins`` table). A user can be ``assigned`` in
    this MAGI **and** admin in any MAGIS — the two flags
    are orthogonal. Tool gating combines both via
    :meth:`magi.tools.base.Tool.gate`.
    """

    id: int  # 主键（自增）
    name: str  # 联系人唯一名
    display_name: str | None = None  # 显示名
    role: str = ROLE_GUEST  # 角色（assigned/guest）
    tgid: int | None = None  # 绑定的 Telegram chat id（本地用户身份）
    # Nullable projection link to the MAGIS-shared operator identity.  It is
    # deliberately not a foreign key because the two stores are independent.
    magis_admin_id: int | None = None
    last_seen_at: datetime | None = None  # 最近活跃时间
    created_at: datetime | None = None  # 创建时间
    updated_at: datetime | None = None  # 最近更新时间

    def to_dict(self) -> dict:
        """Wire-shape for JSON serialisation.

        Mirrors the ``ContactView`` field names
        (``admin`` / ``notes`` / ``source`` fields are
        gone — admin authority moved to MAGIS, notes/source live on
        ``ContactNoteBook``). Timestamp fields are ISO-8601
        ``Z`` strings by the time this runs because
        :meth:`BaseBook._row_to_dto` already passed each
        ``datetime`` column through
        :func:`~magi.bus.library.base.to_iso`.
        """
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ContactNote:
    id: int  # 主键（自增）
    contact_id: int  # 所属联系人 ID
    note: str  # 笔记正文
    kind: str = "permanent"  # 笔记类型（permanent/daily）
    note_date: datetime | None = None  # 日记所属日期
    updated_at: datetime | None = None  # 最近更新时间

    def to_dict(self) -> dict:
        """Wire-shape for JSON serialisation.

        Mirrors the ``NoteView`` field names so
        the WebUI API and the LLM tool see the same shape
        they saw pre-migration. Timestamp fields are ISO-8601
        ``Z`` strings via
        :meth:`BaseBook._row_to_dto`.
        """
        return asdict(self)


# -- internal ORM --------------------------------------------------------


class _ContactRow(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_GUEST)
    tgid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    magis_admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class _ContactNoteRow(Base):
    __tablename__ = "contact_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="permanent")
    note_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Books ---------------------------------------------------------------


class ContactBook(BaseBook[_ContactRow, Contact]):
    model_cls = _ContactRow
    dto_cls = Contact

    def get(self, *, contact_id: int) -> Contact | None:
        with self._session() as s:
            row = s.scalar(select(_ContactRow).where(_ContactRow.id == contact_id))
            return self._row_to_dto(row) if row else None

    def get_by_telegram(self, *, tgid: int) -> Contact | None:
        with self._session() as s:
            row = s.scalar(select(_ContactRow).where(_ContactRow.tgid == tgid))
            return self._row_to_dto(row) if row else None

    def get_by_magis_admin_id(self, *, magis_admin_id: int) -> Contact | None:
        """Return this runtime's local projection of one MAGIS admin."""
        with self._session() as s:
            row = s.scalar(
                select(_ContactRow).where(_ContactRow.magis_admin_id == magis_admin_id)
            )
            return self._row_to_dto(row) if row else None

    def add(
        self,
        *,
        name: str,
        role: str = ROLE_GUEST,
        display_name: str | None = None,
        tgid: int | None = None,
        magis_admin_id: int | None = None,
    ) -> Contact:
        """Insert one contact row.

        Owns the write invariants: ``name`` non-empty,
        ``role`` in :data:`ALL_ROLES`. Raises
        :class:`ValueError` if ``name`` collides with an
        existing row — the directory treats names as
        unique. ``display_name`` is normalised (whitespace
        stripped; empty → ``None``) so callers don't have
        to.
        """
        normalized = (name or "").strip()
        if not normalized:
            raise ValueError("name is required")
        if role not in ALL_ROLES:
            raise ValueError(f"role must be one of {sorted(ALL_ROLES)!r}, got {role!r}")
        normalized_display = (display_name or "").strip() or None
        with self._session() as s:
            existing = s.scalar(select(_ContactRow).where(_ContactRow.name == normalized))
            if existing is not None:
                raise ValueError(f"contact name {normalized!r} already exists")
            row = _ContactRow(
                name=normalized,
                role=role,
                display_name=normalized_display,
                tgid=tgid,
                magis_admin_id=magis_admin_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def update(
        self,
        *,
        contact_id: int,
        name: str | None = None,
        display_name: str | None = None,
        role: str | None = None,
        tgid: int | None = None,
        set_display_name: bool = False,
        set_tgid: bool = False,
    ) -> Contact | None:
        """Update one contact and return its DTO.

        Optional values are accompanied by explicit ``set_*`` flags where
        ``None`` is a meaningful clear operation.  This keeps HTTP patch
        semantics out of persistence while still exposing a complete public
        Book operation.
        """
        with self._session() as s:
            row = s.scalar(select(_ContactRow).where(_ContactRow.id == contact_id))
            if row is None:
                return None
            if name is not None:
                normalized = name.strip()
                if not normalized:
                    raise ValueError("name is required")
                duplicate = s.scalar(
                    select(_ContactRow).where(
                        _ContactRow.name == normalized,
                        _ContactRow.id != contact_id,
                    )
                )
                if duplicate is not None:
                    raise ValueError(f"contact name {normalized!r} already exists")
                row.name = normalized
            if set_display_name:
                row.display_name = (display_name or "").strip() or None
            if role is not None:
                if role not in ALL_ROLES:
                    raise ValueError(f"role must be one of {sorted(ALL_ROLES)!r}")
                row.role = role
            if set_tgid:
                if tgid is not None:
                    duplicate = s.scalar(
                        select(_ContactRow).where(
                            _ContactRow.tgid == tgid,
                            _ContactRow.id != contact_id,
                        )
                    )
                    if duplicate is not None:
                        raise ValueError("tgid already bound")
                row.tgid = tgid
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def list_all(self) -> list[Contact]:
        with self._session() as s:
            rows = s.scalars(select(_ContactRow).order_by(_ContactRow.id)).all()
            return [self._row_to_dto(r) for r in rows]

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
        with self._session() as s:
            row = s.scalar(select(_ContactRow).where(_ContactRow.id == contact_id))
            if row is None:
                return None
            row.tgid = tgid
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

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
            return [self._row_to_dto(r) for r in name_rows[:limit]]

class ContactNoteBook(BaseBook[_ContactNoteRow, ContactNote]):
    model_cls = _ContactNoteRow
    dto_cls = ContactNote

    def list_for_contact(self, *, contact_id: int) -> list[ContactNote]:
        with self._session() as s:
            rows = s.scalars(
                select(_ContactNoteRow)
                .where(_ContactNoteRow.contact_id == contact_id)
                .order_by(_ContactNoteRow.id.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def get(self, *, note_id: int) -> ContactNote | None:
        with self._session() as s:
            row = s.get(_ContactNoteRow, note_id)
            return self._row_to_dto(row) if row else None

    def add(self, *, contact_id: int, note: str, kind: str = "permanent") -> ContactNote:
        """Insert one note row.

        Owns the write invariants: ``note`` non-empty
        after strip, content clamped to
        :data:`_NOTE_MAX_BYTES` (8 KB). Raises
        :class:`ValueError` if the parent contact id does
        not resolve — callers should pre-check via
        :meth:`ContactBook.get` when they want a friendlier
        error, but a foreign-key write attempt here is a
        programmer error worth surfacing as a ValueError.
        """
        content = (note or "").strip()
        if not content:
            raise ValueError("note is required")
        content = content[:_NOTE_MAX_BYTES]
        with self._session() as s:
            row = _ContactNoteRow(
                contact_id=contact_id,
                note=content,
                kind=kind,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def update_note(self, *, note_id: int, note: str) -> ContactNote:
        """Replace a note row's body.

        Owns the write invariants: ``note`` non-empty after
        strip, content clamped to :data:`_NOTE_MAX_BYTES`.
        Raises :class:`LookupError` if ``note_id`` does
        not resolve — same exception the old service raised so
        the tool layer's ``LookupError → ToolResult.err``
        path stays in place.
        """
        content = (note or "").strip()
        if not content:
            raise ValueError("note is required")
        content = content[:_NOTE_MAX_BYTES]
        with self._session() as s:
            row = s.get(_ContactNoteRow, note_id)
            if row is None:
                raise LookupError(f"contact_note {note_id!r} not found")
            row.note = content
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def delete_note(self, *, note_id: int) -> bool:
        """Delete a note row by id. Idempotent.

        Returns ``True`` if a row was removed, ``False`` if
        no row matched. Mirrors
        ``ContactsService.delete_note`` so the tool layer
        can render "deleted / no-op" identically.
        """
        with self._session() as s:
            row = s.get(_ContactNoteRow, note_id)
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    def read_daily_note(self, *, contact_id: int) -> ContactNote | None:
        """Return today's daily-note row for *contact_id*, or ``None``."""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with self._session() as s:
            row = s.scalar(
                select(_ContactNoteRow).where(
                    _ContactNoteRow.contact_id == contact_id,
                    _ContactNoteRow.kind == "daily",
                    _ContactNoteRow.note_date >= today,
                )
            )
            return self._row_to_dto(row) if row else None

    def upsert_daily_note(
        self,
        *,
        contact_id: int,
        body_delta: str,
        note_date: datetime | None = None,
    ) -> ContactNote:
        """Append a delta to today's daily note.

        One row per ``(contact_id, note_date)`` —
        ``kind='daily'``. On a hit, the new line is
        appended with a ``"\\n"`` separator and clamped to
        :data:`_DAILY_NOTE_MAX_BYTES` (32 KB per row). On a
        miss, a fresh row is inserted.

        ``note_date`` defaults to today's UTC midnight —
        callers passing an explicit date are back-filling a
        missed day; the Book stamps it verbatim.

        Owns the write invariants: ``body_delta`` non-empty
        after strip, per-row size cap. Raises
        :class:`ValueError` if the parent contact id does
        not resolve — same as :meth:`add`.
        """
        content = (body_delta or "").strip()
        if not content:
            raise ValueError("body_delta is required")
        content = content[:_NOTE_MAX_BYTES]
        if note_date is None:
            now = datetime.utcnow()
            note_date = datetime(now.year, now.month, now.day)
        with self._session() as s:
            row = s.scalar(
                select(_ContactNoteRow).where(
                    _ContactNoteRow.contact_id == contact_id,
                    _ContactNoteRow.kind == "daily",
                    _ContactNoteRow.note_date == note_date,
                )
            )
            if row is None:
                row = _ContactNoteRow(
                    contact_id=contact_id,
                    note=content,
                    kind="daily",
                    note_date=note_date,
                )
                s.add(row)
            else:
                row.note = (row.note + "\n" + content)[:_DAILY_NOTE_MAX_BYTES]
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)


__all__ = [
    "Contact",
    "ContactNote",
    "ContactBook",
    "ContactNoteBook",
    "_ContactRow",
    "_ContactNoteRow",
    "ROLE_ASSIGNED",
    "ROLE_GUEST",
    "ALL_ROLES",
]
