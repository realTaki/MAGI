"""Telegram outbound HTTP helpers — pure stateless functions.

The daemon-thread listener + inbound handler stack that used to live
here has been replaced by :class:`channels.telegram.worker.TelegramWorker`,
which runs the python-telegram-bot ``Application`` on the composition
root's event loop. This module now only ships the outbound HTTP shims
the worker (and tests) call directly.

Functions
---------
- :func:`send_text_raw` — fire-and-forget ``sendMessage`` over the raw
  Telegram Bot API. No SDK import needed, no event loop coupling.
- :func:`verify_token` — onboarding probe (``getMe``) so the operator
  can confirm the token is live before saving it.

Inbound + lifecycle (formerly `_on_message` / `start_bot` / etc.)
live in :mod:`channels.telegram.worker` instead.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("channels.telegram.bot")

_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


async def verify_token(bot_token: str) -> str:
    """Return the bot's ``@username`` if ``bot_token`` is live.

    ``getMe`` is the lightest Telegram API call; it's exactly what
    the onboarding form does before persisting the token. Raises
    ``RuntimeError`` on any non-2xx so the caller can show the
    operator a precise error.
    """
    import asyncio

    def _probe() -> str:
        url = _TELEGRAM_API_BASE.format(token=bot_token, method="getMe")
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"telegram getMe failed: {body!r}")
        result = body.get("result") or {}
        username = str(result.get("username") or "")
        if not username:
            raise RuntimeError(f"telegram getMe returned no username: {body!r}")
        return username

    return await asyncio.to_thread(_probe)


async def send_text_raw(bot_token: str, chat_id: int, text: str) -> None:
    """POST ``sendMessage`` to Telegram over raw HTTPS.

    No SDK import (the worker loop is shared with python-telegram-bot
    already; this helper avoids double-binding). Threaded via
    ``asyncio.to_thread`` so the bot's ``Application.updater`` loop
    can stay responsive while the request flies out.
    """
    import asyncio

    def _send() -> None:
        url = _TELEGRAM_API_BASE.format(token=bot_token, method="sendMessage")
        payload = json.dumps(
            {"chat_id": chat_id, "text": text},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "TG sendMessage HTTP %s chat=%s body=%r",
                exc.code,
                chat_id,
                body[:200],
            )
            raise
        logger.debug("TG sendMessage OK chat=%s body_len=%d", chat_id, len(body))

    await asyncio.to_thread(_send)


async def get_chat_name_raw(bot_token: str, chat_id: int) -> str | None:
    """Resolve a TG chat's display name via ``getChat``.

    Best-effort: returns ``None`` on any failure (network, non-2xx,
    unknown shape) so callers can fall back to the raw chat_id
    without aborting the onboarding flow. Picks the right field by
    chat type — ``title`` for groups/channels, ``first_name +
    last_name`` for private chats, ``@username`` as the last resort.
    """
    import asyncio

    def _probe() -> str | None:
        url = _TELEGRAM_API_BASE.format(token=bot_token, method="getChat")
        payload = json.dumps({"chat_id": chat_id}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict) or not body.get("ok"):
            return None
        result = body.get("result")
        if not isinstance(result, dict):
            return None
        title = str(result.get("title") or "").strip()
        if title:
            return title
        first = str(result.get("first_name") or "").strip()
        last = str(result.get("last_name") or "").strip()
        full = " ".join(part for part in (first, last) if part)
        if full:
            return full
        username = str(result.get("username") or "").strip()
        return f"@{username}" if username else None

    return await asyncio.to_thread(_probe)


__all__ = ["verify_token", "send_text_raw", "get_chat_name_raw"]
