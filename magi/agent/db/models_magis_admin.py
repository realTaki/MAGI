"""Human administrators of one direct MAGIS.

``MAGISAdmin`` deliberately lives in the MAGIS public database rather than in
the singleton WebUI or in a MAGI's private SQLite file.  A Telegram id is the
portable human identity: local ``Contact`` primary keys are private to each
MAGI and therefore must not be used across nodes.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from magi.agent.db.base import Base, utcnow_naive


class MAGISAdmin(Base):
    """A person allowed to administer one MAGI Society.

    Membership is intentionally direct: neither the MAGIS tree nor an Adam
    role creates an inherited administrator grant.
    """

    __tablename__ = "magis_admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("magis_id", "telegram_id", name="uq_magis_admins_magis_telegram"),
    )
