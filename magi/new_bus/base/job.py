"""Job is something that needs to happen, is happening, or has happened."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Self


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class BookOp(StrEnum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@dataclass
class Job:
    """Generic work Job. Firmware later subclasses this.

    Slots attach to the concrete class, not to an instance.
    """

    payload: dict[str, Any] = field(default_factory=dict)
    publisher: str | None = None
    id: str = ""
    status: JobStatus = JobStatus.PENDING
    created_at: datetime | None = None
    result: Any = None
    error: str | None = None

    @classmethod
    def type_name(cls) -> str:
        return cls.__qualname__

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": self.id,
            "type": type(self).type_name(),
            "status": self.status.value,
            "publisher": self.publisher,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
        }
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        job = cls(
            payload=dict(record.get("payload") or {}),
            publisher=record.get("publisher"),
        )
        job.id = str(record["id"])
        job.status = JobStatus(record["status"])
        job.created_at = _parse_dt(record.get("created_at"))
        job.result = record.get("result")
        job.error = record.get("error")
        return job


@dataclass
class ManageBookJob(Job):
    """Manage a Book. ``op`` is the CRUD verb.

    BUS executes these on publish. External workers never claim them.
    """

    book: str = ""
    op: BookOp = BookOp.READ

    def to_record(self) -> dict[str, Any]:
        record = super().to_record()
        record["book"] = self.book
        record["op"] = self.op.value
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        job = super().from_record(record)
        job.book = str(record.get("book") or "")
        job.op = BookOp(record.get("op") or BookOp.READ)
        return job
