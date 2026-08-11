"""``token_usage`` row writer — bus only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from magi.bus import Bus


def record_token_usage(
    *,
    contact_id: int,
    channel: str,
    provider: str,
    model: str | None,
    usage: dict,
    bus: Bus,
) -> None:
    """Insert one ``token_usage`` row for a successful LLM call."""
    if not hasattr(bus, "token_usage_book"):
        return
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    # ``Book.add`` has no ``channel`` column and no longer takes the
    # whole ``usage`` dict as a single JSON blob — fold channel +
    # any other provider-stuffed keys into ``extra`` so dashboards
    # can still slice by them.
    extra: dict[str, Any] = {"channel": channel} if channel else {}
    for key, value in usage.items():
        if key in {"input_tokens", "output_tokens"}:
            continue
        extra[key] = value
    try:
        bus.token_usage_book.add(
            contact_id=contact_id,
            provider=provider,
            model=model or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            extra=extra or None,
        )
    except Exception:
        pass


__all__ = ["record_token_usage"]
