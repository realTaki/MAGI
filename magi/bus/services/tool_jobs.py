"""Bus service: tool_jobs (durable tool execution queue)."""

from __future__ import annotations

from magi.bus.protocols.tools import ToolClaim
from magi.bus.db.store import BusStore


class ToolJobsService:
    """Lease a tool job, complete it, or mark it for retry/dead-letter."""

    def __init__(self, store: BusStore) -> None:
        self._store = store

    def claim_next(self, worker_id: str, *, lease_seconds: int = 60) -> ToolClaim | None:
        return self._store.claim_next_tool_job(worker_id, lease_seconds=lease_seconds)

    def complete(
        self,
        claim: ToolClaim,
        *,
        content: str,
        is_error: bool = False,
    ) -> None:
        """Complete the claimed job.

        ``bus.store.complete_tool_job`` fires the
        ``TOOL_RESULT_RECEIVED`` signoff automatically -- callers
        do not pass any hook context.
        """
        self._store.complete_tool_job(
            claim,
            content=content,
            is_error=is_error,
        )

    def retry(self, job_id: str) -> None:
        self._store.retry_tool_job(job_id)