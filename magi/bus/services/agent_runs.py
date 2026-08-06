"""Bus service: agent_runs (durable agent turn lifecycle facade).

Wraps the existing :class:`magi.bus.store.BusStore` queue methods and
exposes them as a per-domain facade.  No business rules are added here;
the rules live in :class:`magi.bus.store.BusStore` itself (lease/claim
semantics, idempotency, atomic transition).
"""

from __future__ import annotations

from typing import Any

from magi.bus.protocols.agent import AgentMessage, BusClaim, RunResult
from magi.bus.db.store import BusStore


class AgentRunsService:
    """Durable agent turn queue + run-state façade."""

    def __init__(self, store: BusStore) -> None:
        self._store = store

    def publish_input(self, message: AgentMessage) -> str:
        return self._store.publish_agent_message(message)

    def claim_next(self, worker_id: str, *, lease_seconds: int = 60) -> BusClaim | None:
        return self._store.claim_next_agent_message(worker_id, lease_seconds=lease_seconds)

    def commit_transition(self, event_id: str, **kwargs: Any) -> None:
        self._store.commit_agent_transition(event_id, **kwargs)

    def fail(self, event_id: str, *, error_code: str, error_detail: str) -> None:
        self._store.fail_agent_message(event_id, error_code=error_code, error_detail=error_detail)

    def result(self, run_id: str) -> RunResult | None:
        return self._store.get_run_result(run_id)

    def recover_expired_leases(self) -> int:
        return self._store.recover_expired_leases()

    def expire_a2a(self) -> int:
        return self._store.expire_a2a_invocations()

    def complete_a2a(self, *, reply_to: str, content: str, is_error: bool = False) -> str | None:
        """Commit a peer result and wake the corresponding durable run."""
        return self._store.complete_a2a_invocation(
            reply_to=reply_to,
            content=content,
            is_error=is_error,
        )

    def cancel(self, run_id: str, *, reason: str = "cancelled_by_user") -> bool:
        return self._store.cancel_run(run_id, reason=reason)
