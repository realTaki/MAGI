"""``POST /api/prompts/reload`` — force the prompt
loader to drop its in-memory cache.

Hot-reload is automatic by default: every LLM turn's
``_load`` call stat()s the source ``.md`` / ``.yaml``
file and re-reads if mtime or size changed. This
endpoint exists for two cases:

  1. **Operator wants confirmation now** — the next
     LLM turn would eventually pick up the change,
     but the operator wants to verify the reload path
     fires before sending a real message. Hitting
     ``POST /api/prompts/reload`` evicts the cache
     immediately so the very next call goes through
     the slow path.
  2. **A bulk edit** — an operator replaces all five
     files via ``git pull``. The next turn's stat
     would catch them one at a time, but with five
     files + an in-process mtime cache, the round-trip
     savings are negligible either way; this endpoint
     is more for "I want one log line that says it
     reloaded" than for performance.

Auth: admin-gated, same as every other Adam route.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from magi.agent.prompts import _cache, _cache_lock, reset_cache
from magi.channels.webui.api.departments import AdminGate

logger = logging.getLogger("magi.api.prompts")

router = APIRouter(tags=["prompts"])


@router.post("/prompts/reload", status_code=200)
def reload_prompts(_admin: AdminGate) -> dict:
    """Evict the prompt cache.

    Returns ``{"cleared": <count-of-entries-evicted>}``
    so a curl-based operator gets confirmation. The
    next ``_load`` call walks the slow path and
    re-reads each ``.md`` / ``.yaml`` from disk; if
    the on-disk content hasn't changed, the file is
    re-cached identically.

    The "cleared" count is a snapshot of the cache
    size at the moment of eviction (the count can
    drift by the time we report it if a concurrent
    LLM turn happened to call ``_load`` and populate
    one entry — that's a benign race; the next call
    still walks the slow path because the version
    tuple is empty).
    """
    with _cache_lock:
        cleared = len(_cache)
    reset_cache()
    logger.info(
        "prompt cache evicted via admin reload endpoint "
        "(%d entries dropped)", cleared,
    )
    return {"cleared": cleared}