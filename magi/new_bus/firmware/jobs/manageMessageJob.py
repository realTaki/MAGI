"""Manage a message.

MessageBook is managed with ManageBookJob.
ManageMessageJob is work about a message; workers claim it on ManageMessageJobBoard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

from ...base.backends import Backend
from ...base.errors import InvalidJobError
from ...base.job import BaseJob, BaseJobBoard
from ...base.slot import SlotSpace


@dataclass
class ManageMessageJob(BaseJob):
    """Work about a message. Lives on ManageMessageJobBoard."""

    message_id: str = ""
    session_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        record = super().to_record()
        record["message_id"] = self.message_id
        record["session_id"] = self.session_id
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        job = super().from_record(record)
        job.message_id = str(record.get("message_id") or "")
        session_id = record.get("session_id")
        job.session_id = None if session_id in (None, "") else str(session_id)
        return job


class ManageMessageJobBoard(BaseJobBoard):
    """The claimable board for ManageMessageJob."""

    def __init__(self, job_type: type[BaseJob], backend: Backend, slots: SlotSpace) -> None:
        if job_type is not ManageMessageJob:
            raise InvalidJobError("ManageMessageJobBoard only accepts ManageMessageJob")
        super().__init__(ManageMessageJob, backend, slots)
