"""Manage a conversation.

ConversationBook is managed with ManageBookJob.
ManageConversationJob is work about a conversation; workers claim it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

from ...base.backends import Backend
from ...base.BaseJob import BaseJob, BaseJobBoard
from ...base.errors import InvalidJobError
from ...base.slot import SlotSpace


@dataclass
class ManageConversationJob(BaseJob):
    """Work about a conversation. Lives on ManageConversationJobBoard."""

    conversation_id: int = 0

    def to_record(self) -> dict[str, Any]:
        record = super().to_record()
        record["conversation_id"] = self.conversation_id
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        job = super().from_record(record)
        job.conversation_id = int(record.get("conversation_id") or 0)
        return job


class ManageConversationJobBoard(BaseJobBoard):
    """The claimable board for ManageConversationJob."""

    def __init__(self, job_type: type[BaseJob], backend: Backend, slots: SlotSpace) -> None:
        if job_type is not ManageConversationJob:
            raise InvalidJobError(
                "ManageConversationJobBoard only accepts ManageConversationJob"
            )
        super().__init__(ManageConversationJob, backend, slots)
