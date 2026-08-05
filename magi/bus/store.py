"""SQLite implementation of MAGI's durable local bus.

Every public mutation uses one short database transaction.  The project-wide
SQLite policy (`BEGIN IMMEDIATE`, WAL and a busy timeout) is configured by
``magi.db.engine``; this class adds queue-specific idempotency and leases on
top.  It is deliberately synchronous so it can be called safely by FastAPI,
the Telegram thread and the scheduler's event loop alike.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from magi.bus.protocols.agent import (
    AgentMessage,
    A2AInvocationRequest,
    BusClaim,
    DeliveryClaim,
    RunResult,
)
from magi.bus.protocols.tools import ToolClaim
from magi.bus.models.queue import (
    AgentInbox,
    AgentRun,
    A2AInvocation,
    DeliveryOutbox,
    LLMAttempt,
    RunInput,
    ToolCall,
    ToolJob,
)
from magi.bus.db.base import utcnow_naive
from magi.bus.db.engine import open_session
from magi.bus.models.local.tool import ToolDefinitionRecord
from magi.bus.models.local.hook_signoff import HookSignoff


# Subject-type ↔ hook-point mapping.  Every bus.store boundary
# method declares its (subject_type, hook_point) pair so the
# dispatch helper can stamp the row without each call site
# spelling out the literal.  Hook points keep the original
# ``HookPoint`` enum string values so the public ``hook_plugin_configs``
# table can route plugins without code changes.
SUBJECT_TYPE_FOR_HOOK_POINT: dict[str, str] = {
    "llm.request.prepared": "llm_attempt",
    "llm.response.received": "llm_attempt",
    "tool.call.pending": "tool_job",
    "tool.result.received": "tool_job",
    "delivery.pending": "delivery_outbox",
    "delivery.dispatched": "delivery_outbox",
}


def _dispatch_hook_signoffs(
    *,
    state_dir: str | None,
    subject_type: str,
    subject_id: str,
    hook_point: str,
) -> int:
    """Insert one ``hook_signoffs`` row per enabled plugin subscribed to ``hook_point``.

    Reads the persistent ``hook_plugin_configs`` table and creates
    one pending signoff for each ``enabled`` row whose JSON
    ``hook_points`` list contains the given hook point.  The
    unique constraint on ``(subject_type, subject_id, hook_point,
    plugin_id)`` makes the dispatch idempotent: re-enqueueing the
    same subject (e.g. via a retry path) does not duplicate the
    signoff.

    Returns the number of signoffs inserted.  ``0`` is the
    expected default when no plugin subscribes to the hook point
    — downstream workers don't filter on empty result, the
    WHERE clause just becomes a no-op.
    """
    from magi.bus.models.local.control_plane import HookPluginConfigRow

    now = utcnow_naive()
    inserted = 0
    with open_session(state_dir) as session:
        plugin_rows = session.query(HookPluginConfigRow).filter_by(enabled=True).all()
        for row in plugin_rows:
            hook_points = row.hook_points or []
            if hook_point not in hook_points:
                continue
            # Use raw SQL to leverage the ON CONFLICT clause so a
            # concurrent dispatcher (e.g. retry path) does not
            # raise UniqueConstraint; the existing pending row is
            # left in place.
            from sqlalchemy import text as _text
            session.execute(
                _text(
                    "INSERT OR IGNORE INTO hook_signoffs "
                    "(subject_type, subject_id, hook_point, plugin_id, "
                    " pending, created_at, acked_at) "
                    "VALUES (:st, :sid, :hp, :pid, 1, :now, NULL)"
                ),
                {
                    "st": subject_type,
                    "sid": subject_id,
                    "hp": hook_point,
                    "pid": row.hook_id,
                    "now": now,
                },
            )
            inserted += 1
        session.commit()
    return inserted


def _has_pending_signoff(
    *,
    state_dir: str | None,
    subject_type: str,
    subject_id: str,
) -> bool:
    """Return ``True`` if at least one plugin still owes a signoff for this subject.

    Used by ``claim_next_*`` methods to keep a job invisible to
    downstream workers until every subscribed plugin has acked.
    """
    from sqlalchemy import text as _text

    with open_session(state_dir) as session:
        row = session.execute(
            _text(
                "SELECT 1 FROM hook_signoffs "
                "WHERE subject_type = :st AND subject_id = :sid "
                "AND pending = 1 LIMIT 1"
            ),
            {"st": subject_type, "sid": subject_id},
        ).first()
    return row is not None


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

        Cross-channel idempotency: when the producer supplies
        ``(source_type, source_id, external_event_id)`` and a prior
        inbox row already carries that triple, the same ``run_id`` is
        returned instead of creating a new run. This handles Telegram
        webhook retries, A2A idempotent POSTs, and other producer-side
        redeliveries where the local ``event_id`` differs but the
        upstream event is the same.
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
        idempotency_key = message.idempotency_key or message.event_id
        with open_session(self._state_dir) as session:
            existing = session.scalar(
                select(AgentInbox).where(AgentInbox.event_id == message.event_id)
            )
            if existing is not None:
                return existing.run_id

            # Cross-channel idempotency: same upstream event may be
            # published twice with different ``event_id`` values (e.g.
            # TG update retry, A2A redelivery). Treat it as the same
            # row. Only consulted when the producer supplied the triple.
            if message.source_type and message.source_id and message.external_event_id:
                cross = session.scalar(
                    select(AgentInbox).where(
                        AgentInbox.source_type == message.source_type,
                        AgentInbox.source_id == message.source_id,
                        AgentInbox.external_event_id == message.external_event_id,
                    )
                )
                if cross is not None:
                    return cross.run_id

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
                        deadline_at=message.deadline_at,
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
                    source_type=message.source_type,
                    source_id=message.source_id,
                    external_event_id=message.external_event_id,
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
                    source_event_id=message.external_event_id,
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

    def is_run_within_deadline(self, run_id: str) -> bool:
        """Return whether a run has no deadline or has not expired yet."""
        with open_session(self._state_dir) as session:
            row = session.get(AgentRun, run_id)
            return row is None or row.deadline_at is None or row.deadline_at > utcnow_naive()

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
                if run.status == "cancelled":
                    row.status = "completed"
                    row.leased_by = None
                    row.leased_until = None
                    row.updated_at = now
                    if attempt_id:
                        attempt = session.scalar(select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id))
                        if attempt is not None:
                            attempt.status = "discarded"
                            attempt.response = attempt_result or {"text": reply}
                            attempt.completed_at = now
                    session.commit()
                    return
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
                                event_id=run.root_event_id,
                                payload={"text": reply},
                                idempotency_key=f"reply:{run.run_id}",
                                status="pending",
                                available_at=now,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                elif channel == "a2a":
                    metadata = dict(row.payload.get("metadata") or {})
                    target_magic_id = metadata.get("from_magic_id")
                    if target_magic_id is not None and metadata.get("expect_reply"):
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
                                        "a2a_kind": "result",
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
                # Iteration count + token usage (added 0011).
                run.iteration_count = (run.iteration_count or 0) + 1
                if attempt_result:
                    usage = attempt_result.get("usage") if isinstance(attempt_result, dict) else None
                    if usage is not None:
                        run.token_usage = usage
                session_id = row.payload.get("session_id")
                if session_id:
                    # The final provider response becomes user-visible in the
                    # same transition that marks the run complete.  Channels
                    # must not append a second, non-authoritative copy later.
                    from magi.bus.models.local.session import ChatMessage, ChatSession

                    message_id = run.run_id[-26:]
                    session_exists = session.get(ChatSession, str(session_id)) is not None
                    existing_message = None
                    if session_exists:
                        existing_message = session.scalar(
                            select(ChatMessage).where(
                                ChatMessage.session_id == session_id,
                                ChatMessage.message_id == message_id,
                            )
                        )
                    if existing_message is None and session_exists:
                        from magi.bus.protocols.session import utcnow_iso

                        session.add(
                            ChatMessage(
                                session_id=str(session_id),
                                message_id=message_id,
                                role="assistant",
                                text=reply,
                                ts=utcnow_iso(),
                                content_blocks=list((continuation or {}).get("assistant_blocks") or []),
                                run_id=run.run_id,
                                llm_attempt_id=attempt_id,
                            )
                        )
                run.version += 1
                run.error_code = None
                run.error_detail = None
                run.completed_at = now
                run.updated_at = now
                self._project_task_run(session, row.payload, reply=reply, completed_at=now)
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

    def commit_agent_transition(
        self,
        event_id: str,
        *,
        reply: str | None = None,
        delivery_destination: str | None = None,
        continuation: dict[str, Any] | None = None,
        jobs: list[dict[str, Any]] | None = None,
        a2a_requests: list[A2AInvocationRequest] | None = None,
        attempt_id: str | None = None,
        attempt_result: dict[str, Any] | None = None,
    ) -> None:
        """Commit one actor decision atomically.

        A transition is either terminal (``reply`` is supplied) or parks the
        run while durable effects are enqueued.  Each branch uses one SQLite
        transaction; callers never compose inbox acknowledgement, transcript,
        attempt state, tool/A2A effects, and delivery on their own.
        """
        if reply is not None:
            self.complete_agent_message(
                event_id,
                reply,
                delivery_destination=delivery_destination,
                continuation=continuation,
                attempt_id=attempt_id,
                attempt_result=attempt_result,
            )
            return
        if continuation is None:
            raise ValueError("non-terminal agent transition requires continuation")
        self.wait_for_tools(
            event_id,
            continuation=continuation,
            jobs=jobs or [],
            a2a_requests=a2a_requests,
            attempt_id=attempt_id,
            attempt_result=attempt_result,
        )

    def claim_next_delivery(
        self, worker_id: str, *, lease_seconds: int = 60
    ) -> DeliveryClaim | None:
        """Lease one committed channel delivery for external I/O.

        Rows are invisible until every subscribed plugin has acked
        their ``hook_signoffs`` row (see
        :func:`_dispatch_hook_signoffs`).  The worker that claimed
        the row is allowed to send the IM message; the row's status
        becomes ``processing`` and stays leased until
        :meth:`complete_delivery` settles it.
        """
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
            # Filter on pending signoffs.  If any plugin still owes
            # a signoff for this row, refuse the claim -- the
            # worker is not allowed to send until every plugin
            # has had its chance to observe the row.
            if _has_pending_signoff(
                state_dir=self._state_dir,
                subject_type="delivery_outbox",
                subject_id=row.delivery_id,
            ):
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
        event_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Write a generic delivery effect before any external I/O.

        ``event_id`` and ``idempotency_key`` are partial-unique-index
        de-duplication columns (see ``0009_idempotency_keys`` migration
        docstring). When supplied and an existing row already carries
        the same value, the existing ``delivery_id`` is returned
        unchanged rather than creating a duplicate outbox row.
        """
        delivery_id = delivery_id or _new_id("delivery")
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            if event_id is not None:
                existing = session.scalar(
                    select(DeliveryOutbox).where(DeliveryOutbox.event_id == event_id)
                )
                if existing is not None:
                    return existing.delivery_id
            if idempotency_key is not None:
                existing = session.scalar(
                    select(DeliveryOutbox).where(
                        DeliveryOutbox.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    return existing.delivery_id
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
                        event_id=event_id,
                        payload=payload,
                        idempotency_key=idempotency_key,
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

    def retry_delivery(self, delivery_id: str, *, delay_seconds: int | None = None) -> None:
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(DeliveryOutbox).where(DeliveryOutbox.delivery_id == delivery_id)
            )
            if row is None:
                raise KeyError(f"unknown delivery: {delivery_id}")
            if row.attempts >= 8:
                row.status = "dead"
                row.leased_by = None
                row.leased_until = None
                row.updated_at = now
                session.commit()
                return
            row.status = "retry"
            row.leased_by = None
            row.leased_until = None
            backoff = delay_seconds if delay_seconds is not None else min(300, 5 * (2 ** max(0, row.attempts - 1)))
            row.available_at = now + timedelta(seconds=backoff)
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
        a2a_requests: list[A2AInvocationRequest] | None = None,
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
            if run.status == "cancelled":
                row.status = "completed"
                row.leased_by = None
                row.leased_until = None
                row.updated_at = now
                if attempt_id:
                    attempt = session.scalar(select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id))
                    if attempt is not None:
                        attempt.status = "discarded"
                        attempt.response = attempt_result or {}
                        attempt.completed_at = now
                session.commit()
                return
            row.status = "completed"
            row.leased_by = None
            row.leased_until = None
            row.updated_at = now
            waiting_for_a2a = bool(a2a_requests and any(item.expect_reply for item in a2a_requests))
            run.status = "waiting_a2a" if waiting_for_a2a else "waiting_tool"
            run.continuation = continuation
            run.version += 1
            run.updated_at = now
            # Iteration count + expected ids (added 0011).
            run.iteration_count = (run.iteration_count or 0) + 1
            run.expected_tool_call_ids = [
                str(job["tool_call_id"]) for job in (jobs or [])
            ]
            run.expected_a2a_invocation_ids = [
                str(req.tool_call_id) for req in (a2a_requests or [])
            ]
            if attempt_result:
                usage = (
                    attempt_result.get("usage")
                    if isinstance(attempt_result, dict)
                    else None
                )
                if usage is not None:
                    run.token_usage = usage
            if attempt_id:
                attempt = session.scalar(select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id))
                if attempt is not None:
                    attempt.status = "completed"
                    attempt.response = attempt_result or {}
                    attempt.completed_at = now
            for ordinal, job_data in enumerate(jobs):
                tool_call_id = str(job_data["tool_call_id"])
                # tool_call_id is already UNIQUE; idempotency_key (added
                # in 0009) is the partial unique index that producers
                # supply when the LLM-stable id is not sufficient (e.g.
                # "send this email once"). Default to the tool_call_id
                # so the existing dedup path keeps working.
                idempotency_key = job_data.get("idempotency_key") or f"tool:{run.run_id}:{tool_call_id}"
                if session.scalar(select(ToolJob).where(ToolJob.tool_call_id == tool_call_id)):
                    continue
                if session.scalar(select(ToolJob).where(ToolJob.idempotency_key == idempotency_key)):
                    continue
                # Within-run monotonic ordinal (design §6.6). The
                # loop counter is the canonical source: jobs is a
                # Python list passed by the actor transition in the
                # order the LLM emitted the tool_uses, and the actor
                # never reorders it. Using a Python counter (not a
                # SQL MAX query) avoids the
                # ``max-on-uncommitted-inserts`` problem where the
                # MAX inside one transaction can't see rows added
                # earlier in the same loop.
                next_ordinal = ordinal + 1
                tool_source = job_data.get("tool_source")
                catalog_revision = job_data.get("catalog_revision")
                schema_hash = job_data.get("schema_hash")
                # Resolve the definition in the same transition that creates
                # the effect. The stored fields let the worker refuse a
                # deleted or incompatible implementation later without
                # consulting the agent-visible registry.
                if tool_source is None or catalog_revision is None or schema_hash is None:
                    definition = session.scalar(
                        select(ToolDefinitionRecord).where(
                            ToolDefinitionRecord.name == str(job_data["tool_name"]),
                            ToolDefinitionRecord.enabled.is_(True),
                        )
                    )
                    if definition is not None:
                        tool_source = definition.source
                        catalog_revision = definition.revision
                        schema_hash = definition.schema_hash
                session.add(
                    ToolCall(
                        tool_call_id=tool_call_id,
                        run_id=run.run_id,
                        tool_name=str(job_data["tool_name"]),
                        arguments=dict(job_data["arguments"]),
                        status="requested",
                        ordinal=next_ordinal,
                        ordered_at=now,
                        created_at=now,
                    )
                )
                session.add(
                    ToolJob(
                        job_id=_new_id("tool"),
                        run_id=run.run_id,
                        tool_call_id=tool_call_id,
                        tool_name=str(job_data["tool_name"]),
                        tool_source=tool_source,
                        catalog_revision=catalog_revision,
                        schema_hash=schema_hash,
                        idempotency_key=idempotency_key,
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
            for request in a2a_requests or ():
                invocation_id = _new_id("a2a")
                existing = session.scalar(
                    select(A2AInvocation).where(A2AInvocation.tool_call_id == request.tool_call_id)
                )
                if existing is not None:
                    continue
                session.add(
                    ToolCall(
                        tool_call_id=request.tool_call_id,
                        run_id=run.run_id,
                        tool_name="message_magi",
                        arguments={
                            "magic_id": request.target_magic_id,
                            "text": request.text,
                            "expect_reply": request.expect_reply,
                        },
                        status="requested" if request.expect_reply else "completed",
                        result=None if request.expect_reply else {"content": "message queued for delivery", "is_error": False},
                        created_at=now,
                        completed_at=None if request.expect_reply else now,
                    )
                )
                session.add(
                    A2AInvocation(
                        invocation_id=invocation_id,
                        run_id=run.run_id,
                        target=str(request.target_magic_id),
                        tool_call_id=request.tool_call_id,
                        request_event_id=f"a2a-request:{invocation_id}",
                        reply_to=invocation_id,
                        expect_reply=request.expect_reply,
                        deadline_at=now + timedelta(seconds=request.timeout_seconds) if request.expect_reply else None,
                        idempotency_key=f"a2a:{run.run_id}:{request.tool_call_id}",
                        request={"text": request.text},
                        status="pending",
                        created_at=now,
                    )
                )
                session.add(
                    DeliveryOutbox(
                        delivery_id=f"delivery:{invocation_id}",
                        run_id=run.run_id,
                        channel="a2a",
                        destination=str(request.target_magic_id),
                        event_id=f"a2a:{invocation_id}",
                        payload={
                            "text": request.text,
                            "reply_to": invocation_id if request.expect_reply else None,
                            "a2a_kind": "request",
                            "correlation_id": run.correlation_id,
                        },
                        idempotency_key=f"a2a:{invocation_id}",
                        status="pending",
                        available_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
            if not jobs and not waiting_for_a2a and a2a_requests:
                continue_event_id = f"run-continue:{run.run_id}:{run.version}"
                if session.scalar(select(AgentInbox).where(AgentInbox.event_id == continue_event_id)) is None:
                    session.add(
                        AgentInbox(
                            event_id=continue_event_id,
                            run_id=run.run_id,
                            conversation_id=run.conversation_id,
                            correlation_id=run.correlation_id,
                            kind="tool.result",
                            source_id="message_magi",
                            payload={"text": "", "internal": True},
                            status="pending",
                            available_at=now,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            session.commit()

    # --- LLM attempt lifecycle (Phase B: provider-worker queue) -----------
    #
    # A ``LLMJob`` is one row in ``llm_attempts`` with
    # ``status="queued"`` carrying the serialized request JSON. The
    # worker claims the row, runs the provider, and writes the result
    # back via :meth:`complete_llm_attempt`. ``phase`` carries the
    # caller-supplied ``kind`` string (audit-only label; the worker
    # does not branch on it).

    def enqueue_llm_job(
        self, *, run_id: str, inbox_event_id: str | None, kind: str,
    ) -> str:
        """Insert a queued LLMAttempt row. Returns the new attempt_id."""
        attempt_id = _new_id("llm")
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            session.add(
                LLMAttempt(
                    attempt_id=attempt_id,
                    run_id=run_id,
                    inbox_event_id=inbox_event_id,
                    phase=kind,
                    status="queued",
                    started_at=now,
                )
            )
            session.commit()
        return attempt_id

    def claim_next_llm_job(
        self, worker_id: str, *, lease_seconds: int = 60,
    ) -> tuple[str, str, str | None] | None:
        """Lease the oldest queued LLM attempt.

        Returns ``(attempt_id, run_id, inbox_event_id)`` or ``None``
        if the queue is empty. Crashed workers' leases expire on
        :meth:`recover_expired_llm_job_leases` and the row becomes
        claimable again.
        """
        now = utcnow_naive()
        until = now + timedelta(seconds=lease_seconds)
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(LLMAttempt)
                .where(LLMAttempt.status == "queued")
                .order_by(LLMAttempt.started_at, LLMAttempt.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "claimed"
            row.leased_by = worker_id
            row.leased_until = until
            session.commit()
            return row.attempt_id, row.run_id, row.inbox_event_id

    def complete_llm_attempt(
        self,
        attempt_id: str,
        *,
        response: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Unified terminal write for a claimed/started attempt.

        Exactly one of ``response`` / ``error`` should be set:
        ``response`` for successes, ``error`` for failures. Setting
        neither leaves the row in its current status (useful for
        "discarded" without an explicit terminal state).
        """
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            attempt = session.scalar(
                select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id)
            )
            if attempt is None:
                return
            attempt.completed_at = now
            attempt.leased_until = None
            attempt.leased_by = None
            if response is not None:
                attempt.status = "completed"
                attempt.response = response
                attempt.error = None
            elif error is not None:
                attempt.status = "failed"
                attempt.error = error
                attempt.response = None
            session.commit()

    def recover_expired_llm_job_leases(self) -> int:
        """Return abandoned provider jobs to the queue.

        Called once at worker start. Rows with ``status="claimed"``
        and a lease in the past are set back to ``queued`` so a fresh
        worker can pick them up.
        """
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            rows = list(
                session.scalars(
                    select(LLMAttempt).where(
                        LLMAttempt.status == "claimed",
                        LLMAttempt.leased_until.is_not(None),
                        LLMAttempt.leased_until < now,
                    )
                )
            )
            for row in rows:
                row.status = "queued"
                row.leased_by = None
                row.leased_until = None
            if rows:
                session.commit()
            return len(rows)

    def persist_llm_job_request(
        self, attempt_id: str, *, request: dict[str, Any],
    ) -> None:
        """Write the serialized request onto a queued attempt row."""
        with open_session(self._state_dir) as session:
            attempt = session.scalar(
                select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id)
            )
            if attempt is None:
                return
            attempt.request = request
            session.commit()

    def load_llm_job_request(self, attempt_id: str) -> dict[str, Any] | None:
        """Read back the serialized request the worker should run."""
        with open_session(self._state_dir) as session:
            attempt = session.scalar(
                select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id)
            )
            if attempt is None:
                return None
            return dict(attempt.request) if attempt.request else None

    def load_llm_job_result(
        self,
        attempt_id: str,
        *,
        wait_seconds: float = 30.0,
        poll_seconds: float = 0.1,
    ) -> dict[str, Any] | None:
        """Block until the given attempt reaches a terminal status.

        Returns a dict shaped ``{status, response?, error?}``:

        - ``status == "completed"`` with ``response`` set to the
          worker's terminal write.
        - ``status == "failed"`` with ``error`` set to the worker's
          failure envelope (``{detail, code}``).
        - ``None`` when the deadline passes without the worker
          settling the row (caller falls through to its fallback path,
          mirroring today's "skip compaction on timeout" behaviour).

        Sync SQLite polling — wrap with :func:`asyncio.to_thread` when
        called from an async context. Each tick opens its own
        SQLite session so the worker thread isn't blocked by a held
        reader lock while we sleep.
        """
        import time as _time
        deadline = _time.monotonic() + max(0.0, wait_seconds)
        while _time.monotonic() <= deadline:
            with open_session(self._state_dir) as session:
                attempt = session.scalar(
                    select(LLMAttempt).where(LLMAttempt.attempt_id == attempt_id)
                )
                if attempt is None:
                    return None
                if attempt.status == "completed":
                    return {
                        "status": "completed",
                        "response": dict(attempt.response or {}),
                    }
                if attempt.status == "failed":
                    return {
                        "status": "failed",
                        "error": dict(attempt.error or {}),
                    }
            _time.sleep(poll_seconds)
        return None

    # ---- deprecated thin aliases (kept so PR 2 doesn't have to touch
    # every existing caller; will be removed in Phase D) ----------------

    def start_llm_attempt(self, run_id: str, inbox_event_id: str) -> str:
        """Deprecated. Use :meth:`enqueue_llm_job` instead."""
        return self.enqueue_llm_job(
            run_id=run_id, inbox_event_id=inbox_event_id, kind="chat",
        )

    def fail_llm_attempt(self, attempt_id: str, error: str) -> None:
        """Deprecated. Use :meth:`complete_llm_attempt` with ``error=``."""
        self.complete_llm_attempt(attempt_id, error={"detail": error})

    def load_tool_continuation(
        self, run_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        """Return a resumable continuation only after all expected tools settle.

        Ordering (design §6.6 + §10.4): the canonical order is
        ``tool_calls.ordinal`` ASC within a run. Every row created
        under the current schema has ``ordinal`` populated; pre-0010
        fallback to ``continuation["tool_call_ids"]`` array order is
        gone — fresh installs only.
        """
        with open_session(self._state_dir) as session:
            run = session.get(AgentRun, run_id)
            if run is None or run.status not in {"waiting_tool", "waiting_a2a"} or not run.continuation:
                return None
            continuation = dict(run.continuation)
            call_ids = list(continuation.get("tool_call_ids") or [])
            rows = list(
                session.scalars(select(ToolCall).where(ToolCall.run_id == run_id))
            )
            calls_by_id = {row.tool_call_id: row for row in rows}
            if any(
                call_id not in calls_by_id
                or calls_by_id[call_id].status not in {"completed", "failed"}
                for call_id in call_ids
            ):
                return None

            ordered_call_ids = [
                row.tool_call_id
                for row in sorted(
                    [calls_by_id[cid] for cid in call_ids],
                    key=lambda r: (r.ordinal, r.id),
                )
            ]

            results = []
            for call_id in ordered_call_ids:
                result = calls_by_id[call_id].result or {}
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": str(result.get("content") or ""),
                        "is_error": bool(result.get("is_error")),
                    }
                )
            return continuation, results

    def complete_a2a_invocation(
        self,
        *,
        reply_to: str,
        content: str,
        is_error: bool = False,
    ) -> str | None:
        """Atomically attach a peer result to the invocation that requested it.

        Unknown/late ``reply_to`` values are intentionally ignored.  This is
        the correlation boundary that prevents one peer reply resuming an
        unrelated run.
        """
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            invocation = session.scalar(
                select(A2AInvocation).where(
                    A2AInvocation.invocation_id == reply_to,
                    A2AInvocation.expect_reply.is_(True),
                    A2AInvocation.status.in_(("pending", "accepted")),
                )
            )
            if invocation is None or not invocation.tool_call_id:
                return None
            invocation.status = "failed" if is_error else "completed"
            invocation.result = {"content": content, "is_error": is_error}
            invocation.completed_at = now
            call = session.scalar(
                select(ToolCall).where(ToolCall.tool_call_id == invocation.tool_call_id)
            )
            if call is not None:
                call.status = "failed" if is_error else "completed"
                call.result = {"content": content, "is_error": is_error}
                call.completed_at = now
            event_id = f"a2a-result:{reply_to}"
            if session.scalar(select(AgentInbox).where(AgentInbox.event_id == event_id)) is None:
                run = session.get(AgentRun, invocation.run_id)
                session.add(
                    AgentInbox(
                        event_id=event_id,
                        run_id=invocation.run_id,
                        conversation_id=run.conversation_id if run is not None else None,
                        correlation_id=run.correlation_id if run is not None else None,
                        kind="a2a.result",
                        source_id=reply_to,
                        payload={"text": content, "reply_to": reply_to, "is_error": is_error},
                        status="pending",
                        available_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.commit()
            return invocation.run_id

    def expire_a2a_invocations(self) -> int:
        """Convert elapsed expected peer replies into ordinary failed results."""
        now = utcnow_naive()
        expired: list[str] = []
        with open_session(self._state_dir) as session:
            for invocation in session.scalars(
                select(A2AInvocation).where(
                    A2AInvocation.expect_reply.is_(True),
                    A2AInvocation.status.in_(("pending", "accepted")),
                    A2AInvocation.deadline_at.is_not(None),
                    A2AInvocation.deadline_at < now,
                )
            ):
                expired.append(invocation.invocation_id)
        for invocation_id in expired:
            self.complete_a2a_invocation(
                reply_to=invocation_id,
                content="A2A peer response timed out",
                is_error=True,
            )
        return len(expired)

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
            self._project_task_run(
                session,
                row.payload,
                error=f"{error_code}: {error_detail}",
                completed_at=now,
            )
            session.commit()

    @staticmethod
    def _project_task_run(
        session: Any,
        payload: dict[str, Any],
        *,
        completed_at: datetime,
        reply: str | None = None,
        error: str | None = None,
    ) -> None:
        """Project a task-originated actor terminal state in the same commit."""
        metadata = dict(payload.get("metadata") or {})
        task_run_id = metadata.get("task_run_id")
        task_id = metadata.get("task_id")
        if not task_run_id or not task_id:
            return
        from magi.bus.models.local.task import Task, TaskRun

        task_run = session.get(TaskRun, str(task_run_id))
        task = session.get(Task, str(task_id))
        if task_run is None or task is None:
            return
        finished = completed_at.isoformat()
        task_run.status = "failed" if error else "success"
        task_run.finished_at = finished
        task_run.error = error[:500] if error else None
        task_run.reply_excerpt = (reply or "")[:500] if error is None else None
        started = metadata.get("task_started_at")
        if isinstance(started, str):
            try:
                task_run.latency_ms = max(
                    0,
                    int((completed_at - datetime.fromisoformat(started)).total_seconds() * 1000),
                )
            except (TypeError, ValueError):
                pass
        task.last_run_at = finished
        task.last_status = task_run.status
        task.last_error = task_run.error
        task.consecutive_failures = (task.consecutive_failures + 1) if error else 0

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
                row.status = "dead" if row.attempts >= 8 else "retry"
                row.leased_by = None
                row.leased_until = None
                row.available_at = now + timedelta(seconds=min(300, 5 * (2 ** max(0, row.attempts - 1))))
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

    def cancel_run(self, run_id: str, *, reason: str = "cancelled_by_user") -> bool:
        """Terminally cancel a run without treating ordinary input as cancel.

        Effects already leased by another worker are not forcibly interrupted;
        their late completions are retained for audit but cannot resume this
        terminal run.
        """
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return False
            if run.status in {"completed", "failed", "cancelled"}:
                return run.status == "cancelled"
            run.status = "cancelled"
            run.error_code = "run.cancelled"
            run.error_detail = reason
            run.completed_at = now
            run.updated_at = now
            for inbox in session.scalars(
                select(AgentInbox).where(
                    AgentInbox.run_id == run_id,
                    AgentInbox.status.in_(("pending", "retry")),
                )
            ):
                inbox.status = "completed"
                inbox.updated_at = now
            session.commit()
            return True

    # ───────────────────────────────────────────────────────────── #
    # Tool-job retry (design §11.2 — at-least-once + idempotency)
    # ───────────────────────────────────────────────────────────── #

    # Cap on retries for a single tool call. After this many
    # attempts the job moves to ``status='dead'`` and the actor
    # worker unblocks the run with a synthetic tool.failed event.
    # Matches the design's at-least-once + bounded-retry contract.
    _MAX_TOOL_JOB_ATTEMPTS = 3

    def retry_tool_job(
        self,
        job_id: str,
        *,
        delay_seconds: int | None = None,
    ) -> None:
        """Mark a failed tool job for retry, or dead-letter past the cap.

        Transitions:
          - ``status='failed'`` + ``attempts < _MAX_TOOL_JOB_ATTEMPTS``
            → ``status='retry'``, lease reset, ``available_at`` set
            to ``now + backoff`` so the next ``claim_next_tool_job``
            picks it up.
          - ``status='failed'`` + ``attempts >= _MAX_TOOL_JOB_ATTEMPTS``
            → ``status='dead'`` plus a synthetic ``tool.failed``
            AgentInbox event so the actor worker unblocks the run
            instead of hanging in ``waiting_tool``.

        Backoff matches ``retry_delivery`` / ``retry_agent_message``:
        ``min(300, 5 * 2 ** max(0, row.attempts - 1))`` seconds.
        """
        now = utcnow_naive()
        with open_session(self._state_dir) as session:
            row = session.scalar(select(ToolJob).where(ToolJob.job_id == job_id))
            if row is None:
                raise KeyError(f"unknown tool job: {job_id}")
            if row.attempts >= self._MAX_TOOL_JOB_ATTEMPTS:
                row.status = "dead"
                row.leased_by = None
                row.leased_until = None
                row.updated_at = now
                session.commit()
                # Unblock the run with a synthetic failed result so
                # ``load_tool_continuation`` sees all expected tool
                # calls terminated and ``commit_agent_transition``
                # can resume the actor.
                self._enqueue_synthetic_tool_failure(
                    session, row, reason="dead_lettered"
                )
                session.commit()
                return
            row.status = "retry"
            row.leased_by = None
            row.leased_until = None
            backoff = (
                delay_seconds
                if delay_seconds is not None
                else min(300, 5 * (2 ** max(0, row.attempts - 1)))
            )
            row.available_at = now + timedelta(seconds=backoff)
            row.updated_at = now
            session.commit()

    def _enqueue_synthetic_tool_failure(
        self,
        session,
        row: "ToolJob",
        *,
        reason: str,
    ) -> None:
        """Write a ``tool.failed`` AgentInbox + RunInput for a dead-lettered job.

        Called from :meth:`retry_tool_job` when the tool has exhausted
        its retry budget. The synthetic result is identical in
        shape to a real tool completion's ``tool.result`` event, so
        the actor worker resumes with a normal provider-valid
        ``tool_result`` block listing the dead-letter reason.
        """
        event_id = f"tool-failed:{row.tool_call_id}"
        if session.scalar(select(AgentInbox).where(AgentInbox.event_id == event_id)) is not None:
            return  # already enqueued by a prior dead-letter pass
        received_seq = (
            session.scalar(
                select(func.coalesce(func.max(RunInput.received_seq), 0)).where(
                    RunInput.run_id == row.run_id
                )
            )
            or 0
        ) + 1
        session.add(
            AgentInbox(
                event_id=event_id,
                run_id=row.run_id,
                correlation_id=row.run_id,
                kind="tool.failed",
                source_id=row.tool_call_id,
                payload={
                    "text": f"tool {row.tool_name!r} {reason} after "
                    f"{row.attempts} attempts",
                    "tool_call_id": row.tool_call_id,
                    "tool_name": row.tool_name,
                    "is_error": True,
                    "dead_lettered": True,
                },
                status="pending",
                available_at=utcnow_naive(),
                created_at=utcnow_naive(),
                updated_at=utcnow_naive(),
            )
        )
        # Mirror the tool_call row to ``completed`` so the actor's
        # ``load_tool_continuation`` view sees all expected tool
        # calls in a terminal state. The actual failure detail is
        # already in the agent_inbox payload above; here we just
        # flip the per-row status so the run can advance.
        tool_call = session.scalar(
            select(ToolCall).where(ToolCall.tool_call_id == row.tool_call_id)
        )
        if tool_call is not None and tool_call.status not in {"completed", "failed"}:
            tool_call.status = "failed"
            tool_call.result = {
                "content": f"tool {row.tool_name!r} dead-lettered",
                "is_error": True,
            }
            tool_call.completed_at = utcnow_naive()
        session.add(
            RunInput(
                run_id=row.run_id,
                event_id=event_id,
                kind="tool.failed",
                payload={
                    "text": f"tool {row.tool_name!r} {reason}",
                    "tool_call_id": row.tool_call_id,
                    "tool_name": row.tool_name,
                    "is_error": True,
                },
                received_seq=received_seq,
                status="pending",
                created_at=utcnow_naive(),
            )
        )

    def enqueue_tool_job(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
        idempotency_key: str | None = None,
        tool_source: str | None = None,
        catalog_revision: int | None = None,
        schema_hash: str | None = None,
    ) -> str:
        """Durably schedule a tool effect exactly once.

        De-duplication order (first match wins):

          1. ``idempotency_key`` if supplied (partial unique index).
          2. ``tool_call_id`` (UNIQUE) — backwards-compatible path.

        When a match is found, the existing ``job_id`` is returned
        without creating a new row. The ``idempotency_key`` is
        preferred by design §11.2 because tool implementations may
        declare a more specific retry boundary than the LLM-stable
        ``tool_call_id`` (e.g. "send this email once" handles).
        """
        now = utcnow_naive()
        job_id = _new_id("tool")
        payload = {"arguments": arguments, "context": context}
        with open_session(self._state_dir) as session:
            if idempotency_key is not None:
                existing = session.scalar(
                    select(ToolJob).where(ToolJob.idempotency_key == idempotency_key)
                )
                if existing is not None:
                    return existing.job_id
            existing = session.scalar(
                select(ToolJob).where(ToolJob.tool_call_id == tool_call_id)
            )
            if existing is not None:
                return existing.job_id
            # Within-run ordinal (added 0010). Same monotonic
            # semantics as wait_for_tools: max+1 in this txn.
            next_ordinal = (
                session.scalar(
                    select(func.coalesce(func.max(ToolCall.ordinal), 0))
                    .where(ToolCall.run_id == run_id)
                )
                or 0
            ) + 1
            session.add(
                ToolCall(
                    tool_call_id=tool_call_id,
                    run_id=run_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    status="requested",
                    ordinal=next_ordinal,
                    ordered_at=now,
                    created_at=now,
                )
            )
            session.add(
                ToolJob(
                    job_id=job_id,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    tool_source=tool_source,
                    catalog_revision=catalog_revision,
                    schema_hash=schema_hash,
                    idempotency_key=idempotency_key,
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
                source=row.tool_source,
                catalog_revision=row.catalog_revision,
                schema_hash=row.schema_hash,
            )

    def complete_tool_job(self, claim: ToolClaim, *, content: str, is_error: bool) -> None:
        """Commit a tool result and return it through the agent mailbox.

        The job's status mirrors ``tool_call.status`` (``completed`` on
        success, ``failed`` on error). Retry / dead-letter is the
        caller's responsibility — :meth:`retry_tool_job` is the
        follow-up path; ToolWorker calls it when ``is_error`` and
        the attempt budget has not been exhausted.
        """
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
            # Mirror tool_call.status semantics: errored jobs are
            # ``failed``; successful ones are ``completed``. The
            # follow-up retry path flips this back to ``retry`` (or
            # ``dead`` if the budget is exhausted).
            job.status = "failed" if is_error else "completed"
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
