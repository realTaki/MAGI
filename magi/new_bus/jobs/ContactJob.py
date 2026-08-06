"""ContactJob — 联系人变更作业（同步写）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.base import Base, utcnow_naive
from magi.new_bus.jobs.base import BaseJobQueue


@dataclass(frozen=True, slots=True)
class ContactJob:
    name: str
    person_id: str | None = None
    notes: str | None = None
    contact_id: str | None = None


class _ContactRow(Base):
    __tablename__ = "contact_entries"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    person_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class ContactJobQueue(BaseJobQueue[None, ContactJob, None]):

    def publish(self, job: ContactJob) -> str:
        with self._session() as s:
            if job.contact_id:
                row = s.scalar(
                    select(_ContactRow).where(_ContactRow.contact_id == job.contact_id)
                )
                if row:
                    row.name = job.name
                    row.person_id = job.person_id
                    row.notes = job.notes
                    s.commit()
                    return job.contact_id
            cid = job.contact_id or uuid.uuid4().hex
            s.add(_ContactRow(
                contact_id=cid,
                name=job.name,
                person_id=job.person_id,
                notes=job.notes,
            ))
            s.flush()
            s.commit()
            return cid
