"""``token_usage`` row writer — extracted from the agent
loop for size and testability.

Each successful LLM call writes one row to the
``token_usage`` table so the
``/api/employees/{id}/token-usage`` endpoint can render
weekly / monthly aggregates. The split from
:mod:`magi.agent.loop` is purely about file size (the
loop module was ~1100 lines, well over the 1000-line
ceiling the team uses) — the function is a pure SQL
insert with no hidden state from the loop's locals.
"""

from __future__ import annotations

from magi.agent.db import TokenUsage, open_session


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
    del state_dir  # see docstring above; signature parity only
    in_t = int(usage.get("input_tokens") or 0)
    out_t = int(usage.get("output_tokens") or 0)
    cc_t = int(usage.get("cache_creation_input_tokens") or 0)
    cr_t = int(usage.get("cache_read_input_tokens") or 0)

    with open_session() as session:
        session.add(TokenUsage(
            uid=uid,
            channel=channel,
            provider=provider,
            model=model,
            input_tokens=in_t,
            output_tokens=out_t,
            cache_creation_tokens=cc_t,
            cache_read_tokens=cr_t,
        ))
        session.commit()


__all__ = ["record_token_usage"]
