"""Bus service: token_usage (per-call LLM token usage records)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

class TokenUsageService:
    """Token-usage façade used by ``magi.agent.token_usage``."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def record(
        self,
        *,
        uid: int,
        channel: str,
        provider: str,
        model: str | None,
        usage: dict,
    ) -> None:
        from magi.bus.models.local.token_usage import TokenUsage
        from magi.bus.db import open_session
        from magi.bus.db.base import utcnow_naive
        with open_session(self._state_dir) as session:
            row = TokenUsage(
                uid=uid,
                channel=channel,
                provider=provider,
                model=model,
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            )
            session.add(row)
            session.commit()

    def aggregate(self, *, uid: int, start: datetime, end: datetime) -> tuple[int, int, int]:
        """Return input tokens, output tokens, and call count for a UTC range."""
        from magi.bus.models.local.token_usage import TokenUsage
        from magi.bus.db import open_session

        with open_session(self._state_dir) as session:
            input_tokens, output_tokens, calls = session.execute(
                select(
                    func.coalesce(func.sum(TokenUsage.input_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.output_tokens), 0),
                    func.count(TokenUsage.id),
                ).where(
                    TokenUsage.uid == uid,
                    TokenUsage.ts >= start,
                    TokenUsage.ts <= end,
                )
            ).one()
        return int(input_tokens or 0), int(output_tokens or 0), int(calls or 0)
