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

from magi.bus.contracts import AgentMessage, BusClaim, RunResult
from magi.bus.models import AgentInbox, AgentRun, RunInput
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
