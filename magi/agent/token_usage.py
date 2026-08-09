"""``token_usage`` row writer — new_bus only."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.new_bus import NewBus


def record_token_usage(
    *,
    uid: int,
    channel: str,
    provider: str,
    model: str | None,
    usage: dict,
    bus: "NewBus",
) -> None:
    """Insert one ``token_usage`` row for a successful LLM call."""
    if not hasattr(bus, "token_usage_book"):
        return
    try:
        bus.token_usage_book.add(
            uid=uid, channel=channel, provider=provider,
            model=model or "", usage=usage,
        )
    except Exception:
        pass


__all__ = ["record_token_usage"]
