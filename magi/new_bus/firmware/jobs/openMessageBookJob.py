"""Open MessageBook."""

from __future__ import annotations

from dataclasses import dataclass

from ...base.openBookJob import OpenBookJob, OpenBookJobBoard, OpenBookJobRow
from ..books.messageBook import MessageBook


@dataclass
class OpenMessageBookJob(OpenBookJob):
    """CRUD on MessageBook. BUS executes this on publish."""


class OpenMessageBookJobRow(OpenBookJobRow):
    __tablename__ = "jobs_book_Message"


class OpenMessageBookJobBoard(OpenBookJobBoard):
    job_cls = OpenMessageBookJob
    book_cls = MessageBook
    row_cls = OpenMessageBookJobRow
