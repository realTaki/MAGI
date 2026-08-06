"""AuthCredentialBook — 认证凭证簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class AuthCredential:
    credential_id: str
    magic_id: int
    kind: str


class _AuthCredentialRow(Base):
    __tablename__ = "auth_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    credential_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    magic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class AuthCredentialBook(BaseBook[_AuthCredentialRow, AuthCredential]):
    model_cls = _AuthCredentialRow
    dto_cls = AuthCredential

    def get(self, *, credential_id: str) -> AuthCredential | None:
        with self._session() as s:
            row = s.scalar(
                select(_AuthCredentialRow).where(_AuthCredentialRow.credential_id == credential_id)
            )
            return self._row_to_dto(row) if row else None

    def list_by_magic(self, *, magic_id: int) -> list[AuthCredential]:
        with self._session() as s:
            rows = s.scalars(
                select(_AuthCredentialRow)
                .where(_AuthCredentialRow.magic_id == magic_id)
            ).all()
            return [self._row_to_dto(r) for r in rows]
