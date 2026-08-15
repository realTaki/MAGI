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
``runtime_state.membership_row_id``) all point at ``magis_memberships.id``.

Query keys
----------

- ``magis_id`` — identifies a MAGIS (used for ``list_for_magis``).
- ``magi_id``  — identifies a single MAGI under a MAGIS (this is
  what the old ``magic.id`` semantic became; we drop the ``c`` to
  match the singular ``MAGI``).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    Boolean,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.library.base import BaseBook, BaseRecord, BaseRecordMixin, record
from magi.bus.library.local.settingBook import SettingBook

RESERVED_ROLE_NAMES = frozenset({"ADAM", "EVA"})
DEFAULT_ROLE_INSTRUCTIONS = {
    "ADAM": "You are the team leader for this MAGIS. Coordinate work, clarify goals, and surface conflicts or risks to the administrator.",
    "EVA": "You are a general-purpose member of this MAGIS. Collaborate with the team, carry out assigned work carefully, and report blockers clearly.",
}


# -- public dataclasses --------------------------------------------------


@record
class MagisRole(BaseRecord):
    magis_id: int  # 所属 MAGIS ID
    name: str  # 角色名（ADAM/EVA/...）
    instruction: str = ""  # 角色描述/职责说明
    is_reserved: bool = False  # 是否为保留角色（ADAM/EVA）


@record
class MagisMembership(BaseRecord):
    magis_id: int  # 所属 MAGIS ID
    role_id: int  # 绑定的角色 ID
    responsibility: str = ""  # 协作职责说明（对外可见）


@dataclass(frozen=True, slots=True, kw_only=True)
class MagisCollaborationMember:
    """Public collaborator card rendered into an Agent's MAGIS directory."""

    magi_id: int  # MAGI 身份 ID
    magi_name: str  # MAGI 显示名
    role_name: str  # 角色名
    responsibility: str  # 协作职责


# -- internal ORM --------------------------------------------------------


class _MagisRoleRow(BaseRecordMixin):
    __tablename__ = "magis_roles"

    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("magis_id", "name", name="uq_magis_roles_magis_name"),)


class _MagisMembershipRow(BaseRecordMixin):
    __tablename__ = "magis_memberships"

    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("magis_roles.id", ondelete="RESTRICT"), nullable=False
    )
    responsibility: Mapped[str] = mapped_column(Text, nullable=False, default="")


# -- Books ---------------------------------------------------------------


class MagisRoleBook(BaseBook[_MagisRoleRow, MagisRole]):
    model_cls = _MagisRoleRow
    record_cls = MagisRole

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

    def update(
        self,
        *,
        role_id: int,
        magis_id: int,
        name: str | None = None,
        instruction: str | None = None,
    ) -> MagisRole | None:
        with self._session() as s:
            row = s.scalar(
                select(_MagisRoleRow).where(
                    _MagisRoleRow.id == role_id,
                    _MagisRoleRow.magis_id == magis_id,
                )
            )
            if row is None:
                return None
            if name is not None:
                row.name = name
            if instruction is not None:
                row.instruction = instruction
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def delete(self, *, role_id: int, magis_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(
                select(_MagisRoleRow).where(
                    _MagisRoleRow.id == role_id,
                    _MagisRoleRow.magis_id == magis_id,
                )
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


class MagisMembershipBook(BaseBook[_MagisMembershipRow, MagisMembership]):
    model_cls = _MagisMembershipRow
    record_cls = MagisMembership

    def __init__(self, factory, *, settings_book: SettingBook | None = None) -> None:
        super().__init__(factory)
        # Optional reference to the local SettingBook so
        # :meth:`instruction_context` can read the per-MAGI personal
        # instruction alongside the MAGIS memberships. Injected by
        # the composition root; ``None`` means "personal instruction
        # unavailable" (test / pre-bootstrap).
        self._settings_book = settings_book

    def list_all(self) -> list[MagisMembership]:
        """Every membership row in the DB, ordered by PK.

        Mirrors the ``list_all()`` convention used by
        :meth:`MagisBook.list_all` and :meth:`RuntimeBook.list_all`
        so the MAGIS-side Books share one surface. No per-MAGI scoping, no
        join with ``magis_roles`` / ``magis``; callers needing
        the rendered (magis_name, role_name, instruction) shape
        go through :meth:`instruction_context` per ``magi_id``.
        """
        with self._session() as s:
            rows = s.scalars(select(_MagisMembershipRow).order_by(_MagisMembershipRow.id)).all()
            return [self._row_to_dto(r) for r in rows]

    def list_for_magis(self, *, magis_id: int) -> list[MagisMembership]:
        """All MAGIs registered under a given parent MAGIS."""
        with self._session() as s:
            rows = s.scalars(
                select(_MagisMembershipRow).where(_MagisMembershipRow.magis_id == magis_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_collaboration_directory(self, *, magi_id: int) -> list[MagisCollaborationMember]:
        """Return public collaboration cards for *magi_id*'s direct MAGIS."""
        from magi.bus.library.magis.runtimeBook import _RuntimeRow

        with self._session() as s:
            own = s.scalar(
                select(_MagisMembershipRow).where(_MagisMembershipRow.id == magi_id)
            )
            if own is None:
                return []
            rows = s.execute(
                select(_MagisMembershipRow, _MagisRoleRow, _RuntimeRow)
                .join(_MagisRoleRow, _MagisRoleRow.id == _MagisMembershipRow.role_id)
                .outerjoin(_RuntimeRow, _RuntimeRow.membership_row_id == _MagisMembershipRow.id)
                .where(_MagisMembershipRow.magis_id == own.magis_id)
                .order_by(_MagisMembershipRow.id)
            ).all()
            return [
                MagisCollaborationMember(
                    magi_id=membership.id,
                    magi_name=(
                        (runtime.backend_ref or f"MAGI #{membership.id}")
                        if runtime is not None
                        else f"MAGI #{membership.id}"
                    ),
                    role_name=role.name,
                    responsibility=membership.responsibility,
                )
                for membership, role, runtime in rows
            ]

    def list_instruction_contexts(self) -> list[dict]:
        """Bulk version of :meth:`instruction_context` — joined ``membership × role × magis`` rows for every entry.

        Each returned dict has the same shape as the ``memberships`` list
        :meth:`instruction_context` produces (``magis_name``,
        ``team_instruction``, ``role_name``, ``role_instruction``), one
        per row. Callers that only need one MAGI's context should keep
        using :meth:`instruction_context`; this method is the bulk
        counterpart for paths (e.g.
        :func:`magi.agent.instructions.runtime_instruction_block`) that
        want to render every membership at once.
        """
        from magi.bus.library.magis.magisBook import _MagisRow

        contexts: list[dict] = []
        with self._session() as s:
            rows = s.execute(
                select(_MagisMembershipRow, _MagisRoleRow, _MagisRow)
                .join(
                    _MagisRoleRow,
                    _MagisRoleRow.id == _MagisMembershipRow.role_id,
                )
                .join(
                    _MagisRow,
                    _MagisRow.id == _MagisMembershipRow.magis_id,
                )
                .order_by(_MagisMembershipRow.id)
            ).all()
            for _membership_row, role_row, magis_row in rows:
                contexts.append(
                    {
                        "magis_name": str(getattr(magis_row, "name", "") or ""),
                        "team_instruction": str(getattr(magis_row, "instruction", "") or ""),
                        "role_name": str(getattr(role_row, "name", "") or ""),
                        "role_instruction": str(getattr(role_row, "instruction", "") or ""),
                    }
                )
        return contexts

    def _record_to_row_values(self, record: MagisMembership, session) -> dict:
        role = session.scalar(select(_MagisRoleRow).where(_MagisRoleRow.id == record.role_id))
        if role is None:
            raise LookupError(f"role {record.role_id} not found")
        if role.magis_id != record.magis_id:
            raise ValueError("role must belong to the target MAGIS")
        values = super()._record_to_row_values(record, session)
        values["responsibility"] = record.responsibility.strip()
        return values

    def remove(self, *, magi_id: int) -> bool:
        """Unregister a MAGI by its per-MAGI identity."""
        with self._session() as s:
            row = s.scalar(select(_MagisMembershipRow).where(_MagisMembershipRow.id == magi_id))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    def update_role(self, *, magi_id: int, magis_id: int, role_id: int) -> MagisMembership | None:
        with self._session() as s:
            row = s.scalar(
                select(_MagisMembershipRow).where(
                    _MagisMembershipRow.id == magi_id,
                    _MagisMembershipRow.magis_id == magis_id,
                )
            )
            if row is None:
                return None
            role = s.scalar(select(_MagisRoleRow).where(_MagisRoleRow.id == role_id))
            if role is None:
                raise LookupError(f"role {role_id} not found")
            if role.magis_id != magis_id:
                raise ValueError("role must belong to the target MAGIS")
            row.role_id = role_id
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def update_responsibility(
        self, *, magi_id: int, magis_id: int, responsibility: str
    ) -> MagisMembership | None:
        """Update the public collaboration responsibility of one MAGI."""
        with self._session() as s:
            row = s.scalar(
                select(_MagisMembershipRow).where(
                    _MagisMembershipRow.id == magi_id,
                    _MagisMembershipRow.magis_id == magis_id,
                )
            )
            if row is None:
                return None
            row.responsibility = responsibility.strip()
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    # -- agent-worker-bus.md §6 helper --------------------------------

    def instruction_context(self, *, magi_id: int) -> tuple[str, list[dict]]:
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
                raw = self._settings_book.get_value(key="instruction")
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
                .where(_MagisMembershipRow.id == magi_id)
                .order_by(_MagisMembershipRow.id)
            ).first()
            if row is not None:
                _, role_row, magis_row = row
                memberships.append(
                    {
                        "magis_name": str(getattr(magis_row, "name", "") or ""),
                        "team_instruction": str(getattr(magis_row, "instruction", "") or ""),
                        "role_name": str(getattr(role_row, "name", "") or ""),
                        "role_instruction": str(getattr(role_row, "instruction", "") or ""),
                    }
                )

        return personal, memberships


__all__ = [
    "MagisRole",
    "MagisMembership",
    "MagisCollaborationMember",
    "MagisRoleBook",
    "MagisMembershipBook",
    "_MagisRoleRow",
    "_MagisMembershipRow",
    "RESERVED_ROLE_NAMES",
    "DEFAULT_ROLE_INSTRUCTIONS",
]
