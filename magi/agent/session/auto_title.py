"""Background job: generate a 3-5-word title per chat session.

When the operator sends the *first* user message of a
session, the chat-send endpoint enqueues a :class:`TitleJob`
on :data:`_title_jobs`. The :func:`_title_worker_loop` (one
instance per process, started in the FastAPI lifespan) drains
the queue and runs :func:`_summarize_to_title` for each. The
job makes one LLM call using the operator's per-employee
credentials (no system-default fallback — the chat-send
endpoint already gated that case) and writes the result back
to ``Session.title`` via :meth:`SessionStore.rename`.

Failure mode policy
-------------------

Every error path (LLM network/auth/rate-limit, provider not
configured, empty reply, corrupted session file, missing
session, race lost to a manual rename) is caught by
:func:`_summarize_to_title` and logged at WARNING. The job
never raises out of itself — a single bad job must not kill
the worker loop. Title generation is *best-effort*; failing
it costs the operator a session label (the chat pane falls
back to ``preview``), nothing else.

The chat-send endpoint pairs the title-job write with an
``asyncio.Lock`` (see
:func:`magi.agent.session.session_lock`); the worker
acquires the same lock around its read-then-write so the two
flows never interleave on the same ``(chat_id, session_id)``.
The lock is per session, not global — distinct sessions
remain independent.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from magi.agent.llm.errors import LLMError
from magi.agent.db.engine import require_state_dir
from magi.agent.llm.factory import get_provider
from magi.agent.llm.provider import ChatMessage
from magi.agent.prompts import load_chat_title_prompt
from magi.agent.session.ids import utcnow_iso
from magi.agent.session.store import SessionStore

logger = logging.getLogger("magi.agent.auto_title")


# Title-job payload. Credentials are baked at enqueue time
# (the chat-send endpoint already has them) so the worker
# never has to read the DB just to call the LLM. The
# trade-off: a key rotation between send and worker pickup
# means the worker uses the *old* key. Acceptable — the worker
# makes at most one call per session and the worst case is a
# 401 silently swallowed.
@dataclass(frozen=True)
class TitleJob:
    chat_id: str
    session_id: str
    employee_id: int
    employee_provider: str
    employee_api_key: str
    employee_model: Optional[str] = None


# The queue is intentionally unbounded. v0's per-operator
# session count is bounded (tens-to-hundreds), so a runaway
# producer is not a realistic concern; dropping title jobs on
# pressure is the kind of silent loss we want to avoid.
_title_jobs: "asyncio.Queue[TitleJob]" = asyncio.Queue()

# Module-level worker handle. ``None`` when the worker has
# not been started; ``done()`` is True after a clean shutdown.
_worker_task: Optional[asyncio.Task[None]] = None


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


async def enqueue_title_job(
    chat_id: str,
    session_id: str,
    employee_id: int,
    employee_provider: str,
    employee_api_key: str,
    employee_model: Optional[str] = None,
) -> None:
    """Enqueue a title job.

    Public entry used by the chat-send endpoint. Returning
    immediately (the queue is unbounded; this is a
    ``Queue.put_nowait`` under the hood) means we don't tie
    the request handler to the worker's pace.
    """
    job = TitleJob(
        chat_id=chat_id,
        session_id=session_id,
        employee_id=employee_id,
        employee_provider=employee_provider,
        employee_api_key=employee_api_key,
        employee_model=employee_model,
    )
    await _title_jobs.put(job)
    logger.info(
        "title job enqueued",
        extra={"session_id": session_id, "chat_id": chat_id},
    )


async def start_title_worker() -> None:
    """Spawn the consumer task. Idempotent — a second call is a
    no-op if the worker is already running.

    Called from the FastAPI lifespan; not module-level so it
    runs after ``init_orm`` has prepared the SQLite engine.
    """
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(
        _title_worker_loop(), name="magi-auto-title"
    )
    logger.info("auto-title worker started")


async def stop_title_worker() -> None:
    """Cancel and drain the worker. Called from the FastAPI
    lifespan shutdown.

    We ``cancel()`` the worker task — any in-flight
    :func:`_summarize_to_title` (mid-LLM-call) sees the
    ``CancelledError`` propagate and the queue simply drops
    pending jobs. Pending jobs are lost on shutdown; the
    operator's *next* /send re-enqueues because that's how the
    chat-send endpoint works (``append_messages`` returns the
    updated session, and ``len(messages)`` drives the gate).
    """
    global _worker_task
    if _worker_task is None:
        return
    if not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    _worker_task = None
    logger.info("auto-title worker stopped")


def pending_jobs() -> int:
    """How many jobs are queued but not yet picked up. v0
    diagnostic — surface in admin endpoints later if needed."""
    return _title_jobs.qsize()


# --------------------------------------------------------------------------- #
# consumer + worker
# --------------------------------------------------------------------------- #


async def _title_worker_loop() -> None:
    """Drain the queue forever (or until cancelled)."""
    logger.info("title worker loop started")
    while True:
        try:
            job = await _title_jobs.get()
        except asyncio.CancelledError:
            logger.info("title worker loop cancelled")
            return
        try:
            await _summarize_to_title(job)
        except asyncio.CancelledError:
            # Propagate cancellation up to the outer loop
            # guard so uvicorn shutdown is honored promptly.
            raise
        except Exception:
            # A single bad job must not kill the loop. The
            # inner ``_summarize_to_title`` already logs the
            # specifics at WARNING; this catch is the safety
            # net for anything that escaped.
            logger.exception(
                "title worker caught unhandled exception; continuing",
                extra={"session_id": job.session_id},
            )


async def _summarize_to_title(job: TitleJob) -> None:
    """One-shot: produce a title and persist it.

    Steps:
      1. Wait 5s so the inbound ``append_messages`` from the
         chat-send handler commits cleanly. (Belt + braces;
         the per-session lock would protect us even without
         this delay — but a 0-second delay is racy and a 0s
         worker means every cold start pays the LLM cost on
         the first inbound message.)
      2. Read the session. Bail if missing / corrupt /
         already-titled / no user message.
      3. Build the provider, call ``chat`` once.
      4. Sanitise the reply (strip quotes / extra lines /
         length-clamp to 80).
      5. Acquire the per-session lock and ``rename`` the
         session with ``bump_updated=True``.

    Every step's failure is caught + logged + swallowed at
    WARNING. The only thing that escapes is
    ``asyncio.CancelledError`` so uvicorn shutdown is honored.
    """
    try:
        await asyncio.sleep(5)

        state_dir = _state_dir_for_job()
        store = SessionStore(state_dir)

        sess = store.get(job.chat_id, job.session_id)
        if sess is None:
            logger.info(
                "title skipped: session gone",
                extra={"session_id": job.session_id},
            )
            return
        if sess.title is not None:
            logger.info(
                "title skipped: already set (manual or prior run)",
                extra={"session_id": job.session_id},
            )
            return

        first_user = next(
            (m for m in sess.messages if m.role == "user" and m.text),
            None,
        )
        if first_user is None:
            logger.info(
                "title skipped: no user message",
                extra={"session_id": job.session_id},
            )
            return

        try:
            provider = get_provider(
                job.employee_provider,
                job.employee_api_key,
                job.employee_model,
            )
        except Exception as e:
            # ``get_provider`` raises ``LLMAuthError`` (or
            # similar) on bad credentials. The chat-send
            # endpoint already gated this case earlier (403
            # ``chat.llm_credentials_required``), so reaching
            # here is unexpected — log and bail.
            logger.warning(
                "title skipped: provider construction failed: %s",
                e,
                extra={"session_id": job.session_id},
            )
            return

        try:
            result = await provider.chat(
                system=load_chat_title_prompt(),
                messages=[
                    ChatMessage(
                        role="user",
                        content=first_user.text,
                    ),
                ],
                max_tokens=20,
            )
        except LLMError as e:
            logger.warning(
                "title skipped: LLM call failed (%s: %s)",
                type(e).__name__, e,
                extra={"session_id": job.session_id},
            )
            return
        except Exception:
            # Anything else (network, JSON parse, etc.) is
            # also swallowed — the worker stays alive.
            logger.exception(
                "title skipped: unexpected LLM-call exception",
                extra={"session_id": job.session_id},
            )
            return

        cleaned = _cleanse_title(result.text or "")
        if not cleaned:
            logger.info(
                "title skipped: empty / cleansed-away reply",
                extra={"session_id": job.session_id},
            )
            return

        # Compare-and-set: only write if title is still null.
        # Pre-D.18 this held an ``asyncio.Lock`` and did
        # read-then-write; with SQLite + ``BEGIN IMMEDIATE`` the
        # ``UPDATE … WHERE title IS NULL`` itself is atomic.
        try:
            fresh = store.set_title_if_null(
                job.chat_id, job.session_id, cleaned,
                bump_updated=True,
            )
        except Exception:
            logger.exception(
                "title skipped: rename failed",
                extra={"session_id": job.session_id},
            )
            return
        if fresh is None:
            logger.info(
                "title skipped: lost the race (title was already set)",
                extra={"session_id": job.session_id},
            )
            return
        logger.info(
            "title set",
            extra={
                "session_id": job.session_id,
                "title": cleaned,
                "source": "auto",
            },
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        # Last-resort net for anything that escapes the
        # inner try blocks (e.g. a bad import of the prompts
        # module). The loop guard in ``_title_worker_loop``
        # catches this, but we don't want to leak a long
        # traceback to stderr.
        logger.exception(
            "title worker caught unhandled exception in _summarize_to_title",
            extra={"session_id": job.session_id},
        )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _state_dir_for_job() -> str:
    """Resolve ``MAGI_STATE_DIR`` lazily on each job pickup.

    Reads the env var at job-dequeue time rather than at
    module import, so tests that override the env var mid-run
    see the new value.
    """
    return require_state_dir()


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
    "TitleJob",
    "enqueue_title_job",
    "start_title_worker",
    "stop_title_worker",
    "pending_jobs",
]
