"""Agent-owned background job: generate a 3-5-word chat title.

Phase D — the call goes through the providers queue (publish-and-poll)
instead of owning a private ``asyncio.Queue`` + worker loop. The
:func:`magi.providers.worker.ProvidersWorker` is the single
authoritative consumer for every LLM call in this process.

Public surface:

- :func:`request_session_title` — fire-and-await for one session;
  returns the persisted title or ``None``. Same fire-and-forget
  semantics as the old ``enqueue_title_job`` for the caller in
  :mod:`magi.agent.worker` (it's a one-line ``asyncio.create_task``).
- :func:`_cleanse_title` — pure helper for the LLM reply.
- :func:`enqueue_title_job` — kept as a thin shim around
  :func:`request_session_title` so legacy call-sites still import.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from magi.bus import get_bus
from magi.bus.protocols.llm_jobs import LLMJob
from magi.providers.worker import enqueue_llm_job

logger = logging.getLogger("magi.agent.auto_title")


async def request_session_title(uid: int, session_id: str) -> str | None:
    """Generate + persist a short chat title; return it.

    Returns ``None`` on every failure path (missing session, no user
    message, provider unconfigured, LLM error, timeout). The agent
    loop's "title is best-effort" policy is unchanged — a bad run
    just falls through to the default ``preview`` label.

    Used to be split across ``enqueue_title_job`` + a private worker
    loop + a private queue + ``_summarize_to_title``. Phase D
    collapses all of that into one function which publishes onto
    the providers queue and polls for the result.
    """
    store = get_bus().session
    sess = store.get(uid, session_id)
    if sess is None:
        logger.info("title skipped: session gone", extra={"session_id": session_id})
        return None
    if sess.title is not None:
        logger.info(
            "title skipped: already set (manual or prior run)",
            extra={"session_id": session_id},
        )
        return None
    first_user = next(
        (m for m in sess.messages if m.role == "user" and m.text),
        None,
    )
    if first_user is None:
        logger.info(
            "title skipped: no user message",
            extra={"session_id": session_id},
        )
        return None

    from magi.bus import get_bus_store
    from magi.prompts import load_chat_title_prompt

    job = LLMJob(
        attempt_id="",
        run_id=f"auto-title-{uuid.uuid4().hex}",
        inbox_event_id=None,
        kind="auto_title",
        system=load_chat_title_prompt(),
        messages=({"role": "user", "content": first_user.text},),
        max_tokens=20,
        tools=None,
        streaming=False,
        extra={"uid": uid, "session_id": session_id},
    )
    attempt_id = await enqueue_llm_job(job)

    bus_store = get_bus_store()
    result = await asyncio.to_thread(
        bus_store.load_llm_job_result,
        attempt_id,
        wait_seconds=30.0,
        poll_seconds=0.1,
    )
    if result is None:
        logger.warning(
            "title skipped: provider job %s timed out",
            attempt_id, extra={"session_id": session_id},
        )
        return None
    if result["status"] != "completed":
        logger.warning(
            "title skipped: provider job %s failed (%s)",
            attempt_id,
            result.get("error", {}).get("code", "?"),
            extra={"session_id": session_id},
        )
        return None

    cleaned = _cleanse_title(result["response"].get("text") or "")
    if not cleaned:
        logger.info(
            "title skipped: empty / cleansed-away reply",
            extra={"session_id": session_id},
        )
        return None

    try:
        fresh = store.set_title_if_null(uid, session_id, cleaned, bump_updated=True)
    except Exception:
        logger.exception(
            "title skipped: rename failed",
            extra={"session_id": session_id},
        )
        return None
    if fresh is None:
        logger.info(
            "title skipped: lost the race (title was already set)",
            extra={"session_id": session_id},
        )
        return None
    logger.info(
        "title set",
        extra={
            "session_id": session_id,
            "title": cleaned,
            "source": "auto",
        },
    )
    return cleaned


async def enqueue_title_job(
    delivery_address: str,
    session_id: str,
    uid: int,
) -> None:
    """Backwards-compat shim that fires ``request_session_title``.

    Phase D keeps this name so :mod:`magi.agent.worker` doesn't need
    to change. The ``delivery_address`` is ignored — it's a
    per-channel address that no longer drives session lookup
    (D.23 moved the lookup key to ``uid``).
    """
    asyncio.create_task(
        request_session_title(uid, session_id),
        name=f"magi-title-{session_id}",
    )


def _cleanse_title(raw: str) -> str:
    """Tidy the LLM's reply into a usable title.

    Strips:
      - leading / trailing whitespace
      - common quote characters (``" ' `` ` `` ``) the model
        occasionally wraps output in
      - any extra lines (we keep only the first non-blank)

    Returns ``""`` when the input has no usable content
    after stripping — the caller treats that as "no title".
    """
    lines = [
        ln.strip().strip('"\'“”‘’`')
        for ln in raw.strip().splitlines()
        if ln.strip()
    ]
    if not lines:
        return ""
    return lines[0][:80]


__all__ = [
    "enqueue_title_job",
    "request_session_title",
]
