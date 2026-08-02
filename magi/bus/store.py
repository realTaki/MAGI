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

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from magi.bus.contracts import AgentMessage, BusClaim, DeliveryClaim, RunResult, ToolClaim
from magi.bus.models import (
    AgentInbox,
    AgentRun,
    DeliveryOutbox,
    LLMAttempt,
    RunInput,
    ToolCall,
    ToolJob,
)
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
        conversation_id = message.conversation_id or message.session_id or (
            f"{message.channel}:{message.uid}" if message.uid is not None else None
        )
        payload = {
            "text": message.text,
            "channel": message.channel,
            "session_id": message.session_id,
            "uid": message.uid,
            "caller_role": message.caller_role,
            "metadata": message.metadata,
            "conversation_id": conversation_id,
            "correlation_id": message.correlation_id or message.event_id,
            "causation_id": message.causation_id,
        }
        run_id = _new_id("run")
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            existing = session.scalar(
                select(AgentInbox).where(AgentInbox.event_id == message.event_id)
            )
            if existing is not None:
                return existing.run_id

            active_run = None
            if message.target_run_id:
                active_run = session.get(AgentRun, message.target_run_id)
            elif conversation_id:
                active_run = session.scalar(
                    select(AgentRun)
                    .where(
                        AgentRun.conversation_id == conversation_id,
                        AgentRun.status.in_(("running", "waiting_tool", "waiting_a2a")),
                    )
                    .order_by(AgentRun.created_at.desc())
                    .limit(1)
                )
            # A normal message for an active conversation is steering input.
            # Its inbox row remains a durable acknowledgement item, while the
            # run_input is already attached in this same transaction so a
            # tool result arriving first still sees it.
            is_steer = active_run is not None and message.kind == "channel.message.received"
            if active_run is not None:
                run_id = active_run.run_id
            else:
                session.add(
                    AgentRun(
                        run_id=run_id,
                        root_event_id=message.event_id,
                        conversation_id=conversation_id,
                        correlation_id=message.correlation_id or message.event_id,
                        status="queued",
                        continuation={"kind": message.kind},
                        created_at=now,
                        updated_at=now,
                    )
                )
            received_seq = (
                session.scalar(select(func.max(RunInput.received_seq)).where(RunInput.run_id == run_id))
                or 0
            ) + 1
            session.add(
                AgentInbox(
                    event_id=message.event_id,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    correlation_id=message.correlation_id or message.event_id,
                    causation_id=message.causation_id,
                    kind="run.steer" if is_steer else message.kind,
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
                    kind="run.steer" if is_steer else message.kind,
                    payload=payload,
                    received_seq=received_seq,
                    status="pending",
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
            active_run = session.scalar(
                select(AgentRun)
                .where(AgentRun.status.in_(("running", "waiting_tool", "waiting_a2a")))
                .order_by(AgentRun.started_at, AgentRun.created_at)
                .limit(1)
            )
            query = select(AgentInbox).where(
                AgentInbox.status.in_(("pending", "retry")),
                AgentInbox.available_at <= now,
            )
            if active_run is not None:
                query = query.where(AgentInbox.run_id == active_run.run_id)
            else:
                # Do not resurrect stale result/steering events from a run
                # which is already terminal; only root queued runs may start.
                query = query.outerjoin(AgentRun, AgentInbox.run_id == AgentRun.run_id).where(
                    or_(AgentRun.status == "queued", AgentRun.run_id.is_(None))
                )
            row = session.scalar(query.order_by(AgentInbox.id).limit(1))
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
                conversation_id=row.conversation_id,
            )

    def complete_agent_message(
        self,
        event_id: str,
        reply: str,
        *,
        delivery_destination: str | None = None,
        continuation: dict[str, Any] | None = None,
        attempt_id: str | None = None,
        attempt_result: dict[str, Any] | None = None,
    ) -> None:
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
                delivery_id = None
                channel = str(row.payload.get("channel") or "")
                if channel == "tg" and delivery_destination:
                    delivery_id = f"delivery:{run.run_id}"
                    existing_delivery = session.scalar(
                        select(DeliveryOutbox).where(DeliveryOutbox.delivery_id == delivery_id)
                    )
                    if existing_delivery is None:
                        session.add(
                            DeliveryOutbox(
                                delivery_id=delivery_id,
                                run_id=run.run_id,
                                channel="tg",
                                destination=delivery_destination,
                                payload={"text": reply},
                                status="pending",
                                available_at=now,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                elif channel == "a2a":
                    metadata = dict(row.payload.get("metadata") or {})
                    target_magic_id = metadata.get("from_magic_id")
                    if target_magic_id is not None:
                        delivery_id = f"delivery:{run.run_id}"
                        if session.scalar(
                            select(DeliveryOutbox).where(DeliveryOutbox.delivery_id == delivery_id)
                        ) is None:
                            session.add(
                                DeliveryOutbox(
                                    delivery_id=delivery_id,
                                    run_id=run.run_id,
                                    channel="a2a",
                                    destination=str(target_magic_id),
                                    payload={
                                        "text": reply,
                                        "reply_to": metadata.get("reply_to"),
                                        "correlation_id": run.correlation_id,
                                    },
                                    status="pending",
                                    available_at=now,
                                    created_at=now,
                                    updated_at=now,
                                )
                            )
                run.status = "completed"
                run.result = {"reply": reply, "delivery_id": delivery_id}
                if continuation is not None:
                    run.continuation = continuation
                run.version += 1
                run.error_code = None
                run.error_detail = None
                run.completed_at = now
                run.updated_at = now
            if attempt_id:
                attempt = session.scalar(select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id))
                if attempt is not None:
                    attempt.status = "completed"
                    attempt.response = attempt_result or {"text": reply}
                    attempt.completed_at = now
            # Every durable input attached to this now-terminal run has been
            # represented in the committed continuation.  Acknowledge them
            # together so a duplicate late tool-result cannot start a new turn.
            for pending in session.scalars(
                select(AgentInbox).where(
                    AgentInbox.run_id == row.run_id,
                    AgentInbox.status.in_(("pending", "retry", "processing")),
                )
            ):
                pending.status = "completed"
                pending.leased_by = None
                pending.leased_until = None
                pending.updated_at = now
            for input_row in session.scalars(
                select(RunInput).where(RunInput.run_id == row.run_id, RunInput.status != "consumed")
            ):
                input_row.status = "consumed"
                input_row.context_seq = input_row.received_seq
            session.commit()

    def claim_next_delivery(
        self, worker_id: str, *, lease_seconds: int = 60
    ) -> DeliveryClaim | None:
        """Lease one committed channel delivery for external I/O."""
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(DeliveryOutbox)
                .where(
                    DeliveryOutbox.status.in_(("pending", "retry")),
                    DeliveryOutbox.available_at <= now,
                )
                .order_by(DeliveryOutbox.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "processing"
            row.leased_by = worker_id
            row.leased_until = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            row.updated_at = now
            session.commit()
            return DeliveryClaim(
                delivery_id=row.delivery_id,
                run_id=row.run_id,
                channel=row.channel,
                destination=row.destination,
                payload=dict(row.payload),
                attempts=row.attempts,
            )

    def enqueue_delivery(
        self,
        *,
        channel: str,
        destination: str | None,
        payload: dict[str, Any],
        run_id: str | None = None,
        delivery_id: str | None = None,
    ) -> str:
        """Write a generic delivery effect before any external I/O."""
        delivery_id = delivery_id or _new_id("delivery")
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            existing = session.scalar(
                select(DeliveryOutbox).where(DeliveryOutbox.delivery_id == delivery_id)
            )
            if existing is None:
                session.add(
                    DeliveryOutbox(
                        delivery_id=delivery_id,
                        run_id=run_id,
                        channel=channel,
                        destination=destination,
                        payload=payload,
                        status="pending",
                        available_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.commit()
        return delivery_id

    def complete_delivery(self, delivery_id: str) -> None:
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(DeliveryOutbox).where(DeliveryOutbox.delivery_id == delivery_id)
            )
            if row is None:
                raise KeyError(f"unknown delivery: {delivery_id}")
            row.status = "delivered"
            row.leased_by = None
            row.leased_until = None
            row.updated_at = now
            session.commit()

    def retry_delivery(self, delivery_id: str, *, delay_seconds: int = 5) -> None:
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(DeliveryOutbox).where(DeliveryOutbox.delivery_id == delivery_id)
            )
            if row is None:
                raise KeyError(f"unknown delivery: {delivery_id}")
            row.status = "retry"
            row.leased_by = None
            row.leased_until = None
            row.available_at = now + timedelta(seconds=delay_seconds)
            row.updated_at = now
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

    def pending_steering_inputs(self, run_id: str) -> list[dict[str, Any]]:
        """Return active-run user steering inputs in durable receive order.

        This is intentionally read from ``run_inputs`` rather than inbox
        claim order: a tool result may be claimed before its steering inbox
        acknowledgement, but the provider transcript must still put every
        tool result before every steering message.
        """
        with open_session(self._state_dir) as session:
            rows = session.scalars(
                select(RunInput)
                .where(RunInput.run_id == run_id, RunInput.kind == "run.steer", RunInput.status == "pending")
                .order_by(RunInput.received_seq, RunInput.id)
            )
            return [dict(row.payload) for row in rows]

    def wait_for_tools(
        self,
        event_id: str,
        *,
        continuation: dict[str, Any],
        jobs: list[dict[str, Any]],
        attempt_id: str | None = None,
        attempt_result: dict[str, Any] | None = None,
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
            run.version += 1
            run.updated_at = now
            if attempt_id:
                attempt = session.scalar(select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id))
                if attempt is not None:
                    attempt.status = "completed"
                    attempt.response = attempt_result or {}
                    attempt.completed_at = now
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

    def start_llm_attempt(self, run_id: str, inbox_event_id: str) -> str:
        """Persist an inference lifecycle record before external LLM I/O."""
        attempt_id = _new_id("llm")
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            session.add(
                LLMAttempt(
                    attempt_id=attempt_id,
                    run_id=run_id,
                    inbox_event_id=inbox_event_id,
                    phase="agent.step",
                    status="started",
                    started_at=now,
                )
            )
            session.commit()
        return attempt_id

    def fail_llm_attempt(self, attempt_id: str, error: str) -> None:
        """Record a failed/incomplete inference without committing a draft."""
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            attempt = session.scalar(select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id))
            if attempt is not None:
                attempt.status = "failed"
                attempt.error = {"detail": error}
                attempt.completed_at = now
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
        """Return abandoned inbox, tool and delivery work after a crash."""
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
            tool_rows = list(
                session.scalars(
                    select(ToolJob).where(
                        ToolJob.status == "processing",
                        ToolJob.leased_until.is_not(None),
                        ToolJob.leased_until < now,
                    )
                )
            )
            for row in tool_rows:
                row.status = "retry"
                row.leased_by = None
                row.leased_until = None
                row.available_at = now
                row.updated_at = now
            delivery_rows = list(
                session.scalars(
                    select(DeliveryOutbox).where(
                        DeliveryOutbox.status == "processing",
                        DeliveryOutbox.leased_until.is_not(None),
                        DeliveryOutbox.leased_until < now,
                    )
                )
            )
            for row in delivery_rows:
                row.status = "retry"
                row.leased_by = None
                row.leased_until = None
                row.available_at = now
                row.updated_at = now
            for attempt in session.scalars(select(LLMAttempt).where(LLMAttempt.status.in_(("started", "streaming")))):
                attempt.status = "interrupted"
                attempt.error = {"detail": "runtime restarted before commit"}
                attempt.completed_at = now
            if rows or tool_rows or delivery_rows:
                session.commit()
            return len(rows) + len(tool_rows) + len(delivery_rows)

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
                run = session.get(AgentRun, claim.run_id)
                received_seq = (
                    session.scalar(
                        select(func.max(RunInput.received_seq)).where(RunInput.run_id == claim.run_id)
                    )
                    or 0
                ) + 1
                session.add(
                    AgentInbox(
                        event_id=event_id,
                        run_id=claim.run_id,
                        conversation_id=run.conversation_id if run is not None else None,
                        correlation_id=run.correlation_id if run is not None else None,
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
                        received_seq=received_seq,
                        status="pending",
                        created_at=now,
                    )
                )
            session.commit()
