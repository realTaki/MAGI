"""Manage a message.

MessageBook is managed with ManageBookJob.
ManageMessageJob is work about a message; workers claim it on ManageMessageJobBoard.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...base.backends import Backend
from ...base.BaseJob import BaseJob, BaseJobBoard
from ...base.errors import InvalidJobError
from ...base.slot import SlotSpace


@dataclass
class ManageMessageJob(BaseJob):
    """Work about a message. Lives on ManageMessageJobBoard."""

    message_id: int = 0
    conversation_id: int | None = None


class ManageMessageJobBoard(BaseJobBoard):
    """The claimable board for ManageMessageJob."""

    def __init__(self, job_type: type[BaseJob], backend: Backend, slots: SlotSpace) -> None:
        if job_type is not ManageMessageJob:
            raise InvalidJobError("ManageMessageJobBoard only accepts ManageMessageJob")
        super().__init__(ManageMessageJob, backend, slots)
