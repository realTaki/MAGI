"""Manage a conversation.

ConversationBook is managed with ManageBookJob.
ManageConversationJob is work about a conversation; workers claim it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...base.BaseJob import BaseJob, BaseJobBoard


@dataclass
class ManageConversationJob(BaseJob):
    """Work about a conversation. Lives on ManageConversationJobBoard."""

    conversation_id: int = 0


class ManageConversationJobBoard(BaseJobBoard):
    """The claimable board for ManageConversationJob."""

    job_cls = ManageConversationJob
