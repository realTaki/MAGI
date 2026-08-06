"""AuthCredentialBook — per-UID login credentials (``auth_credentials`` table).

Schema mirrors the old bus's ``auth_credentials`` table.  The
``secret_hash`` column stores a ``scrypt``-encoded credential;
verification lives in :mod:`magi.webui.api.password_utils` (old bus)
or an equivalent new module — new_bus only handles the row-level CRUD.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


PASSWORD = "password"
TG_CODE = "tg_code"
_VALID_KINDS: tuple[str, ...] = (PASSWORD, TG_CODE)


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthCredential:
    id: int
    uid: int
    kind: str
    secret_hash: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _AuthCredentialRow(Base):
    __tablename__ = "auth_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("uid", "kind", name="ux_auth_credentials_uid_kind"),
        Index("ix_auth_credentials_uid", "uid"),
    )


# -- Book ----------------------------------------------------------------


class AuthCredentialBook(BaseBook[_AuthCredentialRow, AuthCredential]):
    model_cls = _AuthCredentialRow
    dto_cls = AuthCredential

    def get(self, *, credential_id: int) -> AuthCredential | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_AuthCredentialRow)
                .where(_AuthCredentialRow.id == credential_id)
            )
            return self._row_to_dto(row) if row else None

    def find(self, *, uid: int, kind: str) -> AuthCredential | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_AuthCredentialRow).where(
                    _AuthCredentialRow.uid == uid,
                    _AuthCredentialRow.kind == kind,
                )
            )
            return self._row_to_dto(row) if row else None

    def list_for_contact(self, *, uid: int) -> list[AuthCredential]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_AuthCredentialRow).where(_AuthCredentialRow.uid == uid)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, uid: int, kind: str, secret_hash: str) -> AuthCredential:
        with self._factory.session() as s:
            row = _AuthCredentialRow(uid=uid, kind=kind, secret_hash=secret_hash)
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def update_hash(self, *, credential_id: int, secret_hash: str) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_AuthCredentialRow)
                .where(_AuthCredentialRow.id == credential_id)
            )
            if row is None:
                return
            row.secret_hash = secret_hash
            s.commit()

    def delete(self, *, credential_id: int) -> bool:
        with self._factory.session() as s:
            row = s.scalar(
                select(_AuthCredentialRow)
                .where(_AuthCredentialRow.id == credential_id)
            )
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


__all__ = ["AuthCredential", "AuthCredentialBook", "_AuthCredentialRow", "PASSWORD", "TG_CODE"]
