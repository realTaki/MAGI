"""Small domain facades over the durable local bus store."""

from __future__ import annotations

from typing import Any

from magi.bus.contracts import AgentMessage, BusClaim, DeliveryClaim, RunResult, ToolClaim
from magi.bus.store import BusStore


class AgentRunsService:
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


class ToolJobsService:
    def __init__(self, store: BusStore) -> None:
        self._store = store

    def claim_next(self, worker_id: str, *, lease_seconds: int = 60) -> ToolClaim | None:
        return self._store.claim_next_tool_job(worker_id, lease_seconds=lease_seconds)

    def complete(self, claim: ToolClaim, *, content: str, is_error: bool = False) -> None:
        self._store.complete_tool_job(claim, content=content, is_error=is_error)

    def retry(self, job_id: str) -> None:
        self._store.retry_tool_job(job_id)


class DeliveryService:
    def __init__(self, store: BusStore) -> None:
        self._store = store

    def enqueue(self, **kwargs: Any) -> str:
        return self._store.enqueue_delivery(**kwargs)

    def claim_next(self, worker_id: str, *, lease_seconds: int = 60) -> DeliveryClaim | None:
        return self._store.claim_next_delivery(worker_id, lease_seconds=lease_seconds)

    def complete(self, delivery_id: str) -> None:
        self._store.complete_delivery(delivery_id)

    def retry(self, delivery_id: str, *, delay_seconds: int | None = None) -> None:
        self._store.retry_delivery(delivery_id, delay_seconds=delay_seconds)


class SettingsService:
    """BUS-owned compatibility facade for local runtime settings."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def get(self, key: str) -> str | None:
        from magi.db.settings import state_get

        return state_get(self._state_dir, key)

    def set(self, key: str, value: str) -> None:
        from magi.db.settings import state_set

        state_set(self._state_dir, key, value)


class ContactsService:
    """Read-only contact facts needed for durable worker authorization."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def role_for(self, uid: int) -> str | None:
        from magi.db import Contact, open_session

        with open_session(self._state_dir) as session:
            contact = session.get(Contact, uid)
            return contact.role if contact is not None else None
