"""Manage a conversation.

ConversationBook is managed with ManageBookJob.
ManageConversationJob is work about a conversation; workers claim it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...base.backends import Backend
from ...base.BaseJob import BaseJob, BaseJobBoard
from ...base.errors import InvalidJobError
from ...base.slot import SlotSpace


@dataclass
class ManageConversationJob(BaseJob):
    """Work about a conversation. Lives on ManageConversationJobBoard."""

    conversation_id: int = 0


class ManageConversationJobBoard(BaseJobBoard):
    """The claimable board for ManageConversationJob."""

    def __init__(self, job_type: type[BaseJob], backend: Backend, slots: SlotSpace) -> None:
        if job_type is not ManageConversationJob:
            raise InvalidJobError(
                "ManageConversationJobBoard only accepts ManageConversationJob"
            )
        super().__init__(ManageConversationJob, backend, slots)
