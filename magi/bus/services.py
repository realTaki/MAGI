"""Small domain facades over the durable local bus store."""

from __future__ import annotations

import os
from dataclasses import dataclass
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

    def compaction_policy(self) -> tuple[int, int, int]:
        """Return ``(window, threshold_pct, keep_recent)`` from local state."""
        from magi.db.runtime_settings import (
            get_compact_context_window,
            get_compact_keep_recent,
            get_compact_threshold_pct,
        )

        return (
            get_compact_context_window(self._state_dir),
            get_compact_threshold_pct(self._state_dir),
            get_compact_keep_recent(self._state_dir),
        )

    def show_daily_note(self) -> tuple[bool, bool]:
        from magi.db.runtime_settings import get_show_daily_note, get_show_daily_note_prompt

        return get_show_daily_note(self._state_dir), get_show_daily_note_prompt(self._state_dir)


class ContactsService:
    """Read-only contact facts needed for durable worker authorization."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def role_for(self, uid: int) -> str | None:
        from magi.db import Contact, open_session

        with open_session(self._state_dir) as session:
            contact = session.get(Contact, uid)
            return contact.role if contact is not None else None


@dataclass(frozen=True, slots=True)
class ProviderConfiguration:
    provider: str
    api_key: str
    model: str | None


class RuntimeIdentityService:
    """MAGIS-backed runtime identity/provider facts exposed as DTOs."""

    @staticmethod
    def _runtime_magic(session):
        from sqlalchemy import select
        from magi.db import MAGIC, MAGIS

        runtime_id = os.environ.get("MAGI_RUNTIME_ID")
        if runtime_id and runtime_id.isdigit():
            return session.get(MAGIC, int(runtime_id))
        root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
        return session.get(MAGIC, root.adam_id) if root and root.adam_id else None

    def provider_configuration(self) -> ProviderConfiguration | None:
        from magi.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = self._runtime_magic(session)
            if magic is None or not magic.provider or not magic.api_key:
                return None
            return ProviderConfiguration(
                provider=str(magic.provider), api_key=str(magic.api_key), model=getattr(magic, "model", None)
            )

    def instruction_context(self) -> tuple[str, list[dict[str, str]]]:
        from sqlalchemy import select
        from magi.db import MAGIS, MAGISMembership, MAGISRole
        from magi.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = self._runtime_magic(session)
            if magic is None:
                return "", []
            row = session.execute(
                select(MAGISMembership, MAGISRole, MAGIS)
                .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
                .join(MAGIS, MAGIS.id == MAGISMembership.magis_id)
                .where(MAGISMembership.magic_id == magic.id)
                .order_by(MAGISMembership.id)
            ).first()
            memberships = [] if row is None else [{
                "magis_name": str(row[2].name),
                "team_instruction": str(row[2].instruction or ""),
                "role_name": str(row[1].name),
                "role_instruction": str(row[1].instruction or ""),
            }]
            return str(magic.instruction or ""), memberships


class TokenUsageService:
    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def record(self, *, uid: int, channel: str, provider: str, model: str | None, usage: dict) -> None:
        from magi.db import TokenUsage, open_session

        with open_session(self._state_dir) as session:
            session.add(TokenUsage(
                uid=uid,
                channel=channel,
                provider=provider,
                model=model,
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            ))
            session.commit()
