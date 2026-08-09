"""MagisBook + MagisAdminBook — the MAGIS tree + which contacts admin which MAGIS.

Two tables — together they track the MAGIS registry as a forest:

- ``magis``       — one row per MAGIS node.  ``parent_id`` is a
  self-FK (``magis.id``) forming the tree; the root row has
  ``parent_id IS NULL``.  Each MAGIS carries a unique ``name`` and
  a default ``instruction``.  ``magis.adam_id`` is a FK pointing at
  one specific MAGI under this MAGIS — see "ADAM pointer" below.
- ``magis_admins``— which external contacts (``uid`` → ``contacts.id``)
  may administer a given MAGIS.  Used by
  :meth:`magi.tools.base.Tool.gate` to fold the MAGIS-level admin
  tag into per-tool role checks.

Schema for the MAGIS registry tables.

ADAM pointer
------------

``magis.adam_id`` is the FK from a MAGIS to its ADAM MAGI's
identity. This FK targets ``magis_memberships.id`` — a row's own
``id`` is the
per-MAGI identity (the parent MAGIS lives in the same row's
``magis_id``).  See :class:`MagisMembershipBook`.  The ADAM's
display name / instruction / LLM credentials do not live here —
they live in the LOCAL :class:`SettingBook` under
:attr:`SettingBook.KNOWN_KEYS`.

Query keys
----------

- ``magis_id``  — identifies a MAGIS node in the tree.
- ``uid``       — identifies a contact (admin lookups).
- The per-MAGI id (formally the ``magis_memberships.id`` of the
  ADAM, when used as the ``adam_id`` pointer) is called ``magi_id``
  at API boundaries — see :class:`MagisMembershipBook`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.library.base import BaseBook
from magi.bus.db.base import Base, utcnow_naive


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Magis:
    id: int
    name: str
    parent_id: int | None = None
    adam_id: int | None = None
    instruction: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MagisAdmin:
    id: int
    uid: int
    magis_id: int
    created_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _MagisRow(Base):
    __tablename__ = "magis"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("magis.id", ondelete="RESTRICT"), nullable=True
    )
    adam_id: Mapped[int | None] = mapped_column(
        ForeignKey("magis_memberships.id", ondelete="SET NULL"), nullable=True
    )
    instruction: Mapped[str] = mapped_column(default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class _MagisAdminRow(Base):
    __tablename__ = "magis_admins"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )


# -- Books ---------------------------------------------------------------


class MagisBook(BaseBook[_MagisRow, Magis]):
    model_cls = _MagisRow
    dto_cls = Magis

    def get(self, *, magis_id: int) -> Magis | None:
        with self._session() as s:
            row = s.scalar(select(_MagisRow).where(_MagisRow.id == magis_id))
            return self._row_to_dto(row) if row else None

    def get_by_name(self, *, name: str) -> Magis | None:
        with self._session() as s:
            row = s.scalar(select(_MagisRow).where(_MagisRow.name == name))
            return self._row_to_dto(row) if row else None

    def get_root(self) -> Magis | None:
        with self._session() as s:
            row = s.scalar(
                select(_MagisRow)
                .where(_MagisRow.parent_id.is_(None))
                .order_by(_MagisRow.id)
            )
            return self._row_to_dto(row) if row else None

    def list_all(self) -> list[Magis]:
        with self._session() as s:
            rows = s.scalars(select(_MagisRow).order_by(_MagisRow.id)).all()
            return [self._row_to_dto(r) for r in rows]

    def list_children(self, *, parent_id: int) -> list[Magis]:
        with self._session() as s:
            rows = s.scalars(
                select(_MagisRow)
                .where(_MagisRow.parent_id == parent_id)
                .order_by(_MagisRow.id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, name: str, parent_id: int | None = None,
            adam_id: int | None = None, instruction: str = "") -> Magis:
        with self._session() as s:
            row = _MagisRow(
                name=name, parent_id=parent_id, adam_id=adam_id,
                instruction=instruction,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def set_adam(self, *, magis_id: int, adam_id: int | None) -> None:
        with self._session() as s:
            row = s.scalar(select(_MagisRow).where(_MagisRow.id == magis_id))
            if row is None:
                return
            row.adam_id = adam_id
            s.commit()


class MagisAdminBook(BaseBook[_MagisAdminRow, MagisAdmin]):
    model_cls = _MagisAdminRow
    dto_cls = MagisAdmin

    def list_for_magis(self, *, magis_id: int) -> list[MagisAdmin]:
        with self._session() as s:
            rows = s.scalars(
                select(_MagisAdminRow)
                .where(_MagisAdminRow.magis_id == magis_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_for_contact(self, *, uid: int) -> list[MagisAdmin]:
        with self._session() as s:
            rows = s.scalars(
                select(_MagisAdminRow)
                .where(_MagisAdminRow.uid == uid)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def is_admin_for(self, *, uid: int) -> bool:
        """True iff ``uid`` is an admin of any MAGIS node.

        Used by :meth:`magi.tools.base.Tool.gate` to fold the
        MAGIS-level admin tag into the per-MAGI role gate —
        a user with ``role='assigned'`` here **and** an admin
        row in ``magis_admins`` satisfies any tool whose
        ``ALLOWED_ROLES`` contains either tag. Tools that
        genuinely require ``admin`` put ``"admin"`` in
        ``ALLOWED_ROLES``; tools open to operators put
        ``"assigned"``. Per-MAGI ``role`` + MAGIS ``admin``
        are orthogonal — see :class:`Contact` docstring.
        """
        with self._session() as s:
            return s.scalar(
                select(_MagisAdminRow.id)
                .where(_MagisAdminRow.uid == uid)
                .limit(1)
            ) is not None

    def add(self, *, uid: int, magis_id: int) -> MagisAdmin:
        with self._session() as s:
            row = _MagisAdminRow(uid=uid, magis_id=magis_id)
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def remove(self, *, uid: int, magis_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(
                select(_MagisAdminRow).where(
                    _MagisAdminRow.uid == uid,
                    _MagisAdminRow.magis_id == magis_id,
                )
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = ["Magis", "MagisAdmin", "MagisBook", "MagisAdminBook", "_MagisRow", "_MagisAdminRow"]