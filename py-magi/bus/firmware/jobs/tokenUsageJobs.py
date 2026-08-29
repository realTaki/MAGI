"""BUS-owned append operation for TokenUsageBook."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ..books.contactBook import ContactRow
from ..books.tokenUsageBook import TokenUsageRow


@dataclass
class RecordTokenUsageJob(BaseJob):
    """Append the accounting payload reported by a completed LLM call."""

    llm_job_id: int = 0
    contact_id: int | None = None
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0
    response_tokens: int = 0


@dataclass
class RecordTokenUsageResult(BaseJobResult):
    pass


class RecordTokenUsageJobRow(BaseJobRow):
    __tablename__ = "jobs_record_token_usage"

    llm_job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_miss_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RecordTokenUsageJobBoard(
    OperateBookJobBoard[RecordTokenUsageJob, RecordTokenUsageResult, RecordTokenUsageJobRow]
):
    job_cls = RecordTokenUsageJob
    result_cls = RecordTokenUsageResult
    row_cls = RecordTokenUsageJobRow

    def _execute(self, session: Session, job: RecordTokenUsageJob) -> RecordTokenUsageResult:
        if job.llm_job_id <= 0:
            return RecordTokenUsageResult(
                status=JobStatus.FAILED, error="llm_job_id must be positive"
            )
        token_counts = (
            job.input_tokens,
            job.output_tokens,
            job.cache_hit_tokens,
            job.cache_miss_tokens,
            job.cache_write_tokens,
            job.thinking_tokens,
            job.response_tokens,
        )
        if min(token_counts) < 0:
            return RecordTokenUsageResult(
                status=JobStatus.FAILED, error="token counts must be non-negative"
            )
        if job.contact_id is not None and session.get(ContactRow, job.contact_id) is None:
            return RecordTokenUsageResult(
                status=JobStatus.FAILED, error=f"contact {job.contact_id} does not exist"
            )
        session.add(
            TokenUsageRow(
                llm_job_id=job.llm_job_id,
                contact_id=job.contact_id,
                provider=job.provider,
                model=job.model,
                input_tokens=job.input_tokens,
                output_tokens=job.output_tokens,
                cache_hit_tokens=job.cache_hit_tokens,
                cache_miss_tokens=job.cache_miss_tokens,
                cache_write_tokens=job.cache_write_tokens,
                thinking_tokens=job.thinking_tokens,
                response_tokens=job.response_tokens,
            )
        )
        return RecordTokenUsageResult()
