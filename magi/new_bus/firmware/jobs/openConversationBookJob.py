"""Open ConversationBook."""

from __future__ import annotations

from dataclasses import dataclass

from ...base.openBookJob import OpenBookJob, OpenBookJobBoard, OpenBookJobRow
from ..books.conversationBook import ConversationBook


@dataclass
class OpenConversationBookJob(OpenBookJob):
    """CRUD on ConversationBook. BUS executes this on publish."""


class OpenConversationBookJobRow(OpenBookJobRow):
    __tablename__ = "jobs_book_Conversation"


class OpenConversationBookJobBoard(OpenBookJobBoard):
    job_cls = OpenConversationBookJob
    book_cls = ConversationBook
    row_cls = OpenConversationBookJobRow
