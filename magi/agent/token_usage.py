"""``token_usage`` row writer for the durable agent runtime.

Each successful LLM call writes one row to the
``token_usage`` table so the
``/api/contacts/{uid}/token-usage`` endpoint can render
weekly / monthly aggregates. The split from
is deliberately a pure SQL insert with no hidden runtime state.
"""

from __future__ import annotations



def record_token_usage(
    state_dir: str,
    *,
    uid: int,
    channel: str,
    provider: str,
    model: str | None,
    usage: dict,
) -> None:
    """Insert one ``token_usage`` row for a successful LLM call.

    Synchronous because we're already past the async boundary
    (the LLM returned). The SQL insert is one row in a
    dedicated table; latency is bounded by SQLite WAL commit
    (~ms). Pushing it onto the asyncio event loop would add
    bookkeeping for no measurable gain.

    ``usage`` keys follow the Anthropic SDK's ``Usage`` shape
    (see :class:`magi.agent.llm.provider.ChatResult.usage`).
    Unknown keys are ignored; missing keys default to 0 so
    a provider that returned no usage metadata still gets a
    row (call count stays honest).

    ``state_dir`` is unused at runtime (the SQL row is a
    process-global write regardless of which MAGI node is
    calling) but kept in the signature so the function can
    be called uniformly with the rest of the agent loop's
    helpers — it documents "this lives in the state_dir's
    DB" without forcing callers to reach into ORM
    internals.

    Raises whatever the ORM raises — caller is responsible
    for swallowing (we don't want a transient DB hiccup to
    break a chat that already succeeded).
    """
    from magi.bus import bootstrap

    bootstrap(state_dir).token_usage.record(
        uid=uid, channel=channel, provider=provider, model=model, usage=usage
    )


__all__ = ["record_token_usage"]
