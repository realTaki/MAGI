"""Manage a message.

MessageBook is managed with ManageBookJob.
ManageMessageJob is work about a message; workers claim it on ManageMessageJobBoard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

from ...base.backends import Backend
from ...base.BaseJob import BaseJob, BaseJobBoard
from ...base.errors import InvalidJobError
from ...base.slot import SlotSpace


@dataclass
class ManageMessageJob(BaseJob):
    """Work about a message. Lives on ManageMessageJobBoard."""

    message_id: int = 0
    conversation_id: int | None = None

    def to_record(self) -> dict[str, Any]:
        record = super().to_record()
        record["message_id"] = self.message_id
        record["conversation_id"] = self.conversation_id
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        job = super().from_record(record)
        job.message_id = int(record.get("message_id") or 0)
        conversation_id = record.get("conversation_id")
        job.conversation_id = None if conversation_id in (None, "", 0) else int(conversation_id)
        return job


class ManageMessageJobBoard(BaseJobBoard):
    """The claimable board for ManageMessageJob."""

    def __init__(self, job_type: type[BaseJob], backend: Backend, slots: SlotSpace) -> None:
        if job_type is not ManageMessageJob:
            raise InvalidJobError("ManageMessageJobBoard only accepts ManageMessageJob")
        super().__init__(ManageMessageJob, backend, slots)
