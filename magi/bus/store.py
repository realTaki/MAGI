"""SQLite implementation of MAGI's durable local bus.

Every public mutation uses one short database transaction.  The project-wide
SQLite policy (`BEGIN IMMEDIATE`, WAL and a busy timeout) is configured by
``magi.db.engine``; this class adds queue-specific idempotency and leases on
top.  It is deliberately synchronous so it can be called safely by FastAPI,
the Telegram thread and the scheduler's event loop alike.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from magi.bus.contracts import AgentMessage, BusClaim, RunResult, ToolClaim
from magi.bus.models import AgentInbox, AgentRun, RunInput, ToolCall, ToolJob
from magi.db.base import utcnow_naive
from magi.db.engine import open_session


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class BusStore:
    """Durable queue and run-state operations for one MAGI SQLite database."""

    def __init__(self, state_dir: str | None = None) -> None:
        self._state_dir = state_dir

    def publish_agent_message(self, message: AgentMessage) -> str:
        """Publish an agent turn exactly once and return its stable ``run_id``.

        Retrying the same producer event is safe: the unique ``event_id``
        returns the original run rather than creating another turn.
        """
        payload = {
            "text": message.text,
            "channel": message.channel,
            "session_id": message.session_id,
            "uid": message.uid,
            "caller_role": message.caller_role,
            "metadata": message.metadata,
        }
        run_id = _new_id("run")
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            existing = session.scalar(
                select(AgentInbox).where(AgentInbox.event_id == message.event_id)
            )
            if existing is not None:
                return existing.run_id

            session.add(
                AgentRun(
                    run_id=run_id,
                    root_event_id=message.event_id,
                    status="queued",
                    continuation={"kind": message.kind},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                AgentInbox(
                    event_id=message.event_id,
                    run_id=run_id,
                    kind=message.kind,
                    source_id=message.source_id,
                    payload=payload,
                    status="pending",
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                RunInput(
                    run_id=run_id,
                    event_id=message.event_id,
                    kind=message.kind,
                    payload=payload,
                    created_at=now,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                # A concurrent producer won the event-id race.  Re-read in a
                # fresh transaction, then give callers the same durable run.
                session.rollback()
                existing = session.scalar(
                    select(AgentInbox).where(AgentInbox.event_id == message.event_id)
                )
                if existing is None:  # pragma: no cover - defensive DB failure
                    raise
                return existing.run_id
        return run_id

    def claim_next_agent_message(
        self, worker_id: str, *, lease_seconds: int = 60
    ) -> BusClaim | None:
        """Lease the next FIFO input, if any.

        The process starts one :class:`AgentWorker`; nevertheless the update
        is conditional so a duplicated process cannot simultaneously own a
        row.  Expired leases are recoverable by :meth:`recover_expired_leases`.
        """
        now = utcnow_naive()
        until = now + timedelta(seconds=lease_seconds)
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(AgentInbox)
                .where(
                    AgentInbox.status.in_(("pending", "retry")),
                    AgentInbox.available_at <= now,
                )
                .order_by(AgentInbox.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "processing"
            row.leased_by = worker_id
            row.leased_until = until
            row.attempts += 1
            row.updated_at = now
            run = session.get(AgentRun, row.run_id)
            if run is not None and run.status == "queued":
                run.status = "running"
                run.started_at = now
                run.updated_at = now
            session.commit()
            return BusClaim(
                event_id=row.event_id,
                run_id=row.run_id,
                kind=row.kind,
                payload=dict(row.payload),
                attempts=row.attempts,
            )

    def complete_agent_message(self, event_id: str, reply: str) -> None:
        """Mark a leased agent input and its run terminally successful."""
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(select(AgentInbox).where(AgentInbox.event_id == event_id))
            if row is None:
                raise KeyError(f"unknown agent inbox event: {event_id}")
            row.status = "completed"
            row.leased_by = None
            row.leased_until = None
            row.updated_at = now
            run = session.get(AgentRun, row.run_id)
            if run is not None:
                run.status = "completed"
                run.result = {"reply": reply}
                run.error_code = None
                run.error_detail = None
                run.completed_at = now
                run.updated_at = now
            session.commit()

    def complete_agent_input(self, event_id: str) -> None:
        """Acknowledge an inbox event while its run remains active."""
        with open_session(self._state_dir) as session:
            row = session.scalar(select(AgentInbox).where(AgentInbox.event_id == event_id))
            if row is None:
                raise KeyError(f"unknown agent inbox event: {event_id}")
            row.status = "completed"
            row.leased_by = None
            row.leased_until = None
            row.updated_at = utcnow_naive()
            session.commit()

    def wait_for_tools(
        self,
        event_id: str,
        *,
        continuation: dict[str, Any],
        jobs: list[dict[str, Any]],
    ) -> None:
        """Atomically persist a continuation and enqueue its tool effects."""
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(select(AgentInbox).where(AgentInbox.event_id == event_id))
            if row is None:
                raise KeyError(f"unknown agent inbox event: {event_id}")
            run = session.get(AgentRun, row.run_id)
            if run is None:
                raise KeyError(f"unknown agent run: {row.run_id}")
            row.status = "completed"
            row.leased_by = None
            row.leased_until = None
            row.updated_at = now
            run.status = "waiting_tool"
            run.continuation = continuation
            run.updated_at = now
            for job_data in jobs:
                tool_call_id = str(job_data["tool_call_id"])
                if session.scalar(select(ToolJob).where(ToolJob.tool_call_id == tool_call_id)):
                    continue
                session.add(
                    ToolCall(
                        tool_call_id=tool_call_id,
                        run_id=run.run_id,
                        tool_name=str(job_data["tool_name"]),
                        arguments=dict(job_data["arguments"]),
                        status="requested",
                        created_at=now,
                    )
                )
                session.add(
                    ToolJob(
                        job_id=_new_id("tool"),
                        run_id=run.run_id,
                        tool_call_id=tool_call_id,
                        tool_name=str(job_data["tool_name"]),
                        payload={
                            "arguments": dict(job_data["arguments"]),
                            "context": dict(job_data["context"]),
                        },
                        status="pending",
                        available_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.commit()

    def load_tool_continuation(
        self, run_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        """Return a resumable continuation only after all expected tools settle."""
        with open_session(self._state_dir) as session:
            run = session.get(AgentRun, run_id)
            if run is None or run.status != "waiting_tool" or not run.continuation:
                return None
            continuation = dict(run.continuation)
            call_ids = list(continuation.get("tool_call_ids") or [])
            calls = {
                row.tool_call_id: row
                for row in session.scalars(select(ToolCall).where(ToolCall.run_id == run_id))
            }
            if any(
                call_id not in calls or calls[call_id].status not in {"completed", "failed"}
                for call_id in call_ids
            ):
                return None
            results = []
            for call_id in call_ids:
                result = calls[call_id].result or {}
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": str(result.get("content") or ""),
                        "is_error": bool(result.get("is_error")),
                    }
                )
            return continuation, results

    def fail_agent_message(self, event_id: str, *, error_code: str, error_detail: str) -> None:
        """Terminally fail a turn while preserving a user-safe error record."""
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(select(AgentInbox).where(AgentInbox.event_id == event_id))
            if row is None:
                raise KeyError(f"unknown agent inbox event: {event_id}")
            row.status = "failed"
            row.leased_by = None
            row.leased_until = None
            row.updated_at = now
            run = session.get(AgentRun, row.run_id)
            if run is not None:
                run.status = "failed"
                run.error_code = error_code
                run.error_detail = error_detail
                run.completed_at = now
                run.updated_at = now
            session.commit()

    def retry_agent_message(self, event_id: str, *, delay_seconds: int = 0) -> None:
        """Release a transiently failed event for a later claim."""
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(select(AgentInbox).where(AgentInbox.event_id == event_id))
            if row is None:
                raise KeyError(f"unknown agent inbox event: {event_id}")
            row.status = "retry"
            row.leased_by = None
            row.leased_until = None
            row.available_at = now + timedelta(seconds=delay_seconds)
            row.updated_at = now
            run = session.get(AgentRun, row.run_id)
            if run is not None:
                run.status = "queued"
                run.updated_at = now
            session.commit()

    def recover_expired_leases(self) -> int:
        """Return abandoned work to the queue after a worker/process crash."""
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            rows = list(
                session.scalars(
                    select(AgentInbox).where(
                        AgentInbox.status == "processing",
                        AgentInbox.leased_until.is_not(None),
                        AgentInbox.leased_until < now,
                    )
                )
            )
            for row in rows:
                row.status = "retry"
                row.leased_by = None
                row.leased_until = None
                row.available_at = now
                row.updated_at = now
                run = session.get(AgentRun, row.run_id)
                if run is not None and run.status == "running":
                    run.status = "queued"
                    run.updated_at = now
            if rows:
                session.commit()
            return len(rows)

    def get_run_result(self, run_id: str) -> RunResult | None:
        """Read a run state without coupling callers to SQLAlchemy models."""
        with open_session(self._state_dir) as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return None
            result: dict[str, Any] = run.result or {}
            reply = result.get("reply")
            return RunResult(
                run_id=run.run_id,
                status=run.status,
                reply=reply if isinstance(reply, str) else None,
                error_code=run.error_code,
                error_detail=run.error_detail,
            )

    def enqueue_tool_job(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Durably schedule a tool effect exactly once by ``tool_call_id``."""
        now = utcnow_naive()
        job_id = _new_id("tool")
        payload = {"arguments": arguments, "context": context}
        with open_session(self._state_dir) as session:
            existing = session.scalar(select(ToolJob).where(ToolJob.tool_call_id == tool_call_id))
            if existing is not None:
                return existing.job_id
            session.add(
                ToolCall(
                    tool_call_id=tool_call_id,
                    run_id=run_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    status="requested",
                    created_at=now,
                )
            )
            session.add(
                ToolJob(
                    job_id=job_id,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    payload=payload,
                    status="pending",
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        return job_id

    def claim_next_tool_job(self, worker_id: str, *, lease_seconds: int = 60) -> ToolClaim | None:
        """Lease one pending tool job; execution itself remains transaction-free."""
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(ToolJob)
                .where(ToolJob.status.in_(("pending", "retry")), ToolJob.available_at <= now)
                .order_by(ToolJob.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "processing"
            row.leased_by = worker_id
            row.leased_until = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            row.updated_at = now
            tool_call = session.scalar(
                select(ToolCall).where(ToolCall.tool_call_id == row.tool_call_id)
            )
            if tool_call is not None:
                tool_call.status = "running"
            session.commit()
            return ToolClaim(
                job_id=row.job_id,
                run_id=row.run_id,
                tool_call_id=row.tool_call_id,
                tool_name=row.tool_name,
                payload=dict(row.payload),
                attempts=row.attempts,
            )

    def complete_tool_job(self, claim: ToolClaim, *, content: str, is_error: bool) -> None:
        """Commit a tool result and return it through the agent mailbox."""
        now = utcnow_naive()
        event_id = f"tool-result:{claim.tool_call_id}"
        payload = {
            "text": content,
            "tool_call_id": claim.tool_call_id,
            "tool_name": claim.tool_name,
            "is_error": is_error,
        }
        with open_session(self._state_dir) as session:
            job = session.scalar(select(ToolJob).where(ToolJob.job_id == claim.job_id))
            if job is None:
                raise KeyError(f"unknown tool job: {claim.job_id}")
            job.status = "completed"
            job.leased_by = None
            job.leased_until = None
            job.updated_at = now
            tool_call = session.scalar(
                select(ToolCall).where(ToolCall.tool_call_id == claim.tool_call_id)
            )
            if tool_call is not None:
                tool_call.status = "failed" if is_error else "completed"
                tool_call.result = {"content": content, "is_error": is_error}
                tool_call.completed_at = now
            existing = session.scalar(select(AgentInbox).where(AgentInbox.event_id == event_id))
            if existing is None:
                session.add(
                    AgentInbox(
                        event_id=event_id,
                        run_id=claim.run_id,
                        kind="tool.result",
                        source_id=claim.tool_call_id,
                        payload=payload,
                        status="pending",
                        available_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    RunInput(
                        run_id=claim.run_id,
                        event_id=event_id,
                        kind="tool.result",
                        payload=payload,
                        created_at=now,
                    )
                )
            session.commit()
