"""Bus service: token_usage (per-call LLM token usage records)."""

from __future__ import annotations

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
        from magi.db import TokenUsage, open_session
        from magi.db.base import utcnow_naive
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
