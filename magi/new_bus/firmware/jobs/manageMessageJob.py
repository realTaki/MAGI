"""Manage a message.

MessageBook is managed with ManageBookJob.
ManageMessageJob is work about a message; workers claim it on ManageMessageJobBoard.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...base.BaseJob import BaseJob, BaseJobBoard


@dataclass
class ManageMessageJob(BaseJob):
    """Work about a message. Lives on ManageMessageJobBoard."""

    message_id: int = 0
    conversation_id: int | None = None


class ManageMessageJobBoard(BaseJobBoard):
    """The claimable board for ManageMessageJob."""

    job_cls = ManageMessageJob
