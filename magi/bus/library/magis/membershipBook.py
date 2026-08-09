"""MagisMembershipBook + MagisRoleBook — register MAGIs under a MAGIS.

Two tables, both scoped to one parent ``MAGIS``:

- ``magis_memberships`` — one row per MAGI instance.  A row binds
  ``(magis_id, role_id)`` and the row's own ``id`` *is* the per-MAGI
  identity (no separate ``magic`` table — see FK note below).
- ``magis_roles``     — the role vocabulary a parent MAGIS offers.
  ``MagisRoleBook.add`` creates the reserved roles (``ADAM``,
  ``EVA``); custom roles are also allowed.

Schema for MAGIS membership tables. Per-MAGI runtime config
(display ``name``, ``instruction``, LLM ``provider`` / ``api_key``)
does NOT live here — it lives in the LOCAL :class:`SettingBook`
under the ``SettingBook.KNOWN_KEYS`` keys.  This Book only tracks
the per-MAGI identity + role binding.

FKs that target a per-MAGI identity (``magis.adam_id``,
``eva_runtimes.magic_id``) all point at ``magis_memberships.id``.

Query keys
----------

- ``magis_id`` — identifies a MAGIS (used for ``list_for_magis``).
- ``magi_id``  — identifies a single MAGI under a MAGIS (this is
  what the old ``magic.id`` semantic became; we drop the ``c`` to
  match the singular ``MAGI``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.library.base import BaseBook
from magi.bus.db.base import Base, utcnow_naive


RESERVED_ROLE_NAMES = frozenset({"ADAM", "EVA"})
DEFAULT_ROLE_INSTRUCTIONS = {
    "ADAM": "You are the team leader for this MAGIS. Coordinate work, clarify goals, and surface conflicts or risks to the administrator.",
    "EVA": "You are a general-purpose member of this MAGIS. Collaborate with the team, carry out assigned work carefully, and report blockers clearly.",
}


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class MagisRole:
    id: int
    magis_id: int
    name: str
    instruction: str = ""
    is_reserved: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MagisMembership:
    id: int
    magis_id: int
    role_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _MagisRoleRow(Base):
    __tablename__ = "magis_roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("magis_id", "name", name="uq_magis_roles_magis_name"),
    )


class _MagisMembershipRow(Base):
    __tablename__ = "magis_memberships"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("magis_roles.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Books ---------------------------------------------------------------


class MagisRoleBook(BaseBook[_MagisRoleRow, MagisRole]):
    model_cls = _MagisRoleRow
    dto_cls = MagisRole

    def get(self, *, role_id: int) -> MagisRole | None:
        with self._session() as s:
            row = s.scalar(
                select(_MagisRoleRow).where(_MagisRoleRow.id == role_id)
            )
            return self._row_to_dto(row) if row else None

    def list_for_magis(self, *, magis_id: int) -> list[MagisRole]:
        with self._session() as s:
            rows = s.scalars(
                select(_MagisRoleRow)
                .where(_MagisRoleRow.magis_id == magis_id)
                .order_by(_MagisRoleRow.name)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def find(self, *, magis_id: int, name: str) -> MagisRole | None:
        with self._session() as s:
            row = s.scalar(
                select(_MagisRoleRow).where(
                    _MagisRoleRow.magis_id == magis_id,
                    _MagisRoleRow.name == name,
                )
            )
            return self._row_to_dto(row) if row else None

    def add(self, *, magis_id: int, name: str,
            instruction: str = "", is_reserved: bool = False) -> MagisRole:
        with self._session() as s:
            row = _MagisRoleRow(
                magis_id=magis_id, name=name,
                instruction=instruction, is_reserved=is_reserved,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def update(self, *, role_id: int, magis_id: int, name: str | None = None,
               instruction: str | None = None) -> MagisRole | None:
        with self._session() as s:
            row = s.scalar(select(_MagisRoleRow).where(
                _MagisRoleRow.id == role_id, _MagisRoleRow.magis_id == magis_id,
            ))
            if row is None:
                return None
            if name is not None:
                row.name = name
            if instruction is not None:
                row.instruction = instruction
            s.commit(); s.refresh(row)
            return self._row_to_dto(row)

    def delete(self, *, role_id: int, magis_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(select(_MagisRoleRow).where(
                _MagisRoleRow.id == role_id, _MagisRoleRow.magis_id == magis_id,
            ))
            if row is None:
                return False
            s.delete(row); s.commit(); return True


class MagisMembershipBook(BaseBook[_MagisMembershipRow, MagisMembership]):
    model_cls = _MagisMembershipRow
    dto_cls = MagisMembership

    def __init__(self, factory, *, settings_book: "object | None" = None) -> None:
        super().__init__(factory)
        # Optional reference to the local SettingBook so
        # :meth:`instruction_context` can read the per-MAGI personal
        # instruction alongside the MAGIS memberships. Injected by
        # the composition root; ``None`` means "personal instruction
        # unavailable" (test / pre-bootstrap).
        self._settings_book = settings_book

    def get(self, *, magi_id: int) -> MagisMembership | None:
        """Look up a single MAGI under any MAGIS by its per-MAGI identity.

        ``magi_id`` is the ``magis_memberships.id`` (the row's own
        PK, which is what used to be ``magic.id`` before the
        ``magic`` table was retired).
        """
        with self._session() as s:
            row = s.scalar(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.id == magi_id)
            )
            return self._row_to_dto(row) if row else None

    def list_for_magis(self, *, magis_id: int) -> list[MagisMembership]:
        """All MAGIs registered under a given parent MAGIS."""
        with self._session() as s:
            rows = s.scalars(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.magis_id == magis_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, magis_id: int, role_id: int) -> MagisMembership:
        """Register a new MAGI under ``magis_id`` with ``role_id``.

        The MAGI's own identity is assigned by the DB and comes back
        as ``dto.id`` — keep that id for later lookup and for use as
        a FK target from elsewhere (``magis.adam_id``,
        ``eva_runtimes.magic_id``).
        """
        with self._session() as s:
            role = s.scalar(
                select(_MagisRoleRow).where(_MagisRoleRow.id == role_id)
            )
            if role is None:
                raise LookupError(f"role {role_id} not found")
            if role.magis_id != magis_id:
                raise ValueError("role must belong to the target MAGIS")
            row = _MagisMembershipRow(magis_id=magis_id, role_id=role_id)
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def remove(self, *, magi_id: int) -> bool:
        """Unregister a MAGI by its per-MAGI identity."""
        with self._session() as s:
            row = s.scalar(
                select(_MagisMembershipRow)
                .where(_MagisMembershipRow.id == magi_id)
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    def update_role(self, *, magi_id: int, magis_id: int, role_id: int) -> MagisMembership | None:
        with self._session() as s:
            row = s.scalar(select(_MagisMembershipRow).where(
                _MagisMembershipRow.id == magi_id,
                _MagisMembershipRow.magis_id == magis_id,
            ))
            if row is None:
                return None
            role = s.scalar(
                select(_MagisRoleRow).where(_MagisRoleRow.id == role_id)
            )
            if role is None:
                raise LookupError(f"role {role_id} not found")
            if role.magis_id != magis_id:
                raise ValueError("role must belong to the target MAGIS")
            row.role_id = role_id
            s.commit(); s.refresh(row)
            return self._row_to_dto(row)

    # -- agent-worker-bus.md §6 helper --------------------------------

    def instruction_context(self, *, magic_id: int) -> tuple[str, list[dict]]:
        """Return ``(personal_instruction, memberships)`` for one MAGI.

        ``personal_instruction`` is read from the local
        ``SettingBook["instruction"]`` key (the per-MAGI field
        formerly on the old ``magic`` row); ``memberships`` is a
        list with one dict per membership row, each containing
        ``magis_name``, ``team_instruction``, ``role_name``,
        ``role_instruction``.

        Used by :func:`magi.agent.instructions.runtime_instruction_block`
        to assemble the agent's "Instructions" block (design §2.5).
        """
        personal = ""
        if self._settings_book is not None:
            try:
                raw = self._settings_book.get(key="instruction")
                if raw:
                    personal = str(raw)
            except Exception:
                personal = ""

        memberships: list[dict] = []
        with self._session() as s:
            # Single SELECT joining membership + role; the
            # ``magis`` row's name + instruction come from a second
            # hop on the same session (all three tables share the
            # magis factory so they're in the same MetaData).
            from magi.bus.library.magis.magisBook import _MagisRow

            row = s.execute(
                select(_MagisMembershipRow, _MagisRoleRow, _MagisRow)
                .join(
                    _MagisRoleRow,
                    _MagisRoleRow.id == _MagisMembershipRow.role_id,
                )
                .join(
                    _MagisRow,
                    _MagisRow.id == _MagisMembershipRow.magis_id,
                )
                .where(_MagisMembershipRow.id == magic_id)
                .order_by(_MagisMembershipRow.id)
            ).first()
            if row is not None:
                _, role_row, magis_row = row
                memberships.append({
                    "magis_name": str(getattr(magis_row, "name", "") or ""),
                    "team_instruction": str(
                        getattr(magis_row, "instruction", "") or ""
                    ),
                    "role_name": str(getattr(role_row, "name", "") or ""),
                    "role_instruction": str(
                        getattr(role_row, "instruction", "") or ""
                    ),
                })

        return personal, memberships


__all__ = [
    "MagisRole",
    "MagisMembership",
    "MagisRoleBook",
    "MagisMembershipBook",
    "_MagisRoleRow",
    "_MagisMembershipRow",
    "RESERVED_ROLE_NAMES",
    "DEFAULT_ROLE_INSTRUCTIONS",
]
