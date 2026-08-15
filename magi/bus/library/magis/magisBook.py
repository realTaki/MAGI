"""MagisBook + MagisAdminBook — the MAGIS tree + its administrators.

Two tables — together they track the MAGIS registry as a forest:

- ``magis``       — one row per MAGIS node.  ``parent_id`` is a
  self-FK (``magis.id``) forming the tree; the root row has
  ``parent_id IS NULL``.  Each MAGIS carries a unique ``name`` and
  a default ``instruction``.  ``magis.adam_id`` is a FK pointing at
  one specific MAGI under this MAGIS — see "ADAM pointer" below.
- ``magis_admins``— MAGIS-scoped administrator identities.  They do not
  reference a per-MAGI ``contacts`` row: administrator authority and its IM
  verification factor belong to the Society, while contacts are people a
  particular MAGI serves.

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
- ``admin_id``         — identifies a MAGIS administrator.
- The per-MAGI id (formally the ``magis_memberships.id`` of the
  ADAM, when used as the ``adam_id`` pointer) is called ``magi_id``
  at API boundaries — see :class:`MagisMembershipBook`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.library.base import BaseBook

AUTH_MODE_LOCAL_NO_2FA = "local_no_2fa"
AUTH_MODE_IM_2FA_ENABLED = "im_2fa_enabled"
AUTH_MODE_RECOVERY_LOCAL_NO_2FA = "recovery_local_no_2fa"
AUTH_MODE_DISABLED = "disabled"
ALL_ADMIN_AUTH_MODES = frozenset(
    {
        AUTH_MODE_LOCAL_NO_2FA,
        AUTH_MODE_IM_2FA_ENABLED,
        AUTH_MODE_RECOVERY_LOCAL_NO_2FA,
        AUTH_MODE_DISABLED,
    }
)

# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Magis:
    id: int  # MAGIS 主键
    name: str  # MAGIS 唯一名
    parent_id: int | None = None  # 父 MAGIS ID（根节点为 NULL）
    adam_id: int | None = None  # ADAM MAGI 的身份 ID
    instruction: str = ""  # MAGIS 默认指令
    created_at: datetime | None = None  # 创建时间
    updated_at: datetime | None = None  # 最近更新时间


@dataclass(frozen=True, slots=True)
class MagisAdmin:
    id: int  # 主键
    magis_id: int  # 授权作用的 MAGIS ID
    name: str  # 管理员显示名
    tgid: int | None = None  # 已绑定 Telegram 验证地址
    auth_mode: str = AUTH_MODE_LOCAL_NO_2FA
    created_at: datetime | None = None  # 授权时间


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class _MagisAdminRow(Base):
    __tablename__ = "magis_admins"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    tgid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    auth_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AUTH_MODE_LOCAL_NO_2FA
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


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
                select(_MagisRow).where(_MagisRow.parent_id.is_(None)).order_by(_MagisRow.id)
            )
            return self._row_to_dto(row) if row else None

    def root_runtime_url(self, *, magi_id: int) -> str | None:
        """Return the K8s service URL for the root MAGIS's ADAM, when applicable.

        The WebUI control plane uses this as the platform-neutral fallback
        when a runtime row is missing from ``runtime_state_book``: in K8s
        deployments the root MAGI is reached via the service DNS name
        (``magi`` by default, overridable via ``MAGI_ROOT_RUNTIME_URL``),
        not via the local-mode ``base_url`` written by the launcher.  Local
        callers never reach this path — ``runtime_state_book.get()``
        resolves first and short-circuits with ``base_url``.
        """
        import os

        root = self.get_root()
        if root is not None and root.adam_id == magi_id:
            return os.environ.get("MAGI_ROOT_RUNTIME_URL", "http://magi:42069")
        return None

    def list_all(self) -> list[Magis]:
        with self._session() as s:
            rows = s.scalars(select(_MagisRow).order_by(_MagisRow.id)).all()
            return [self._row_to_dto(r) for r in rows]

    def add(
        self,
        *,
        name: str,
        parent_id: int | None = None,
        adam_id: int | None = None,
        instruction: str = "",
    ) -> Magis:
        with self._session() as s:
            row = _MagisRow(
                name=name,
                parent_id=parent_id,
                adam_id=adam_id,
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

    def update(
        self,
        *,
        magis_id: int,
        name: str | None = None,
        parent_id: int | None = None,
        instruction: str | None = None,
        set_parent_id: bool = False,
    ) -> Magis | None:
        """Update one MAGIS row and return a DTO, never an ORM row."""
        with self._session() as s:
            row = s.scalar(select(_MagisRow).where(_MagisRow.id == magis_id))
            if row is None:
                return None
            if name is not None:
                row.name = name
            if instruction is not None:
                row.instruction = instruction
            if set_parent_id:
                row.parent_id = parent_id
            s.commit()
            s.refresh(row)
            return self._row_to_dto(row)

    def delete(self, *, magis_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(select(_MagisRow).where(_MagisRow.id == magis_id))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


class MagisAdminBook(BaseBook[_MagisAdminRow, MagisAdmin]):
    model_cls = _MagisAdminRow
    dto_cls = MagisAdmin

    def list_for_magis(self, *, magis_id: int) -> list[MagisAdmin]:
        with self._session() as s:
            rows = s.scalars(
                select(_MagisAdminRow).where(_MagisAdminRow.magis_id == magis_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def get(self, *, admin_id: int) -> MagisAdmin | None:
        with self._session() as s:
            row = s.get(_MagisAdminRow, admin_id)
            return self._row_to_dto(row) if row else None

    def get_by_tgid(self, *, magis_id: int, tgid: int) -> MagisAdmin | None:
        with self._session() as s:
            row = s.scalar(
                select(_MagisAdminRow).where(
                    _MagisAdminRow.magis_id == magis_id,
                    _MagisAdminRow.tgid == tgid,
                )
            )
            return self._row_to_dto(row) if row else None

    def add(
        self,
        *,
        magis_id: int,
        name: str,
        tgid: int | None = None,
        auth_mode: str = AUTH_MODE_LOCAL_NO_2FA,
    ) -> MagisAdmin:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("admin name is required")
        if auth_mode not in ALL_ADMIN_AUTH_MODES:
            raise ValueError("invalid admin auth_mode")
        with self._session() as s:
            if tgid is not None:
                duplicate = s.scalar(
                    select(_MagisAdminRow).where(
                        _MagisAdminRow.magis_id == magis_id,
                        _MagisAdminRow.tgid == tgid,
                    )
                )
                if duplicate is not None:
                    raise ValueError("tgid already bound to a MAGIS admin")
            row = _MagisAdminRow(
                magis_id=magis_id,
                name=normalized_name,
                tgid=tgid,
                auth_mode=auth_mode,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def set_auth_mode(self, *, admin_id: int, auth_mode: str) -> MagisAdmin:
        if auth_mode not in ALL_ADMIN_AUTH_MODES:
            raise ValueError("invalid admin auth_mode")
        with self._session() as s:
            row = s.get(_MagisAdminRow, admin_id)
            if row is None:
                raise LookupError(f"MAGIS admin {admin_id!r} not found")
            row.auth_mode = auth_mode
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def bind_telegram(self, *, admin_id: int, tgid: int) -> MagisAdmin:
        with self._session() as s:
            row = s.get(_MagisAdminRow, admin_id)
            if row is None:
                raise LookupError(f"MAGIS admin {admin_id!r} not found")
            duplicate = s.scalar(
                select(_MagisAdminRow).where(
                    _MagisAdminRow.magis_id == row.magis_id,
                    _MagisAdminRow.tgid == tgid,
                    _MagisAdminRow.id != admin_id,
                )
            )
            if duplicate is not None:
                raise ValueError("tgid already bound to a MAGIS admin")
            row.tgid = tgid
            row.auth_mode = AUTH_MODE_IM_2FA_ENABLED
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def remove_by_id(self, *, admin_id: int, magis_id: int) -> bool:
        with self._session() as s:
            row = s.scalar(
                select(_MagisAdminRow).where(
                    _MagisAdminRow.id == admin_id,
                    _MagisAdminRow.magis_id == magis_id,
                )
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = [
    "ALL_ADMIN_AUTH_MODES",
    "AUTH_MODE_DISABLED",
    "AUTH_MODE_IM_2FA_ENABLED",
    "AUTH_MODE_LOCAL_NO_2FA",
    "AUTH_MODE_RECOVERY_LOCAL_NO_2FA",
    "Magis",
    "MagisAdmin",
    "MagisBook",
    "MagisAdminBook",
    "_MagisRow",
    "_MagisAdminRow",
]
