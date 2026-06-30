"""Onboarding API — two-step flow for the IM bot token.

    1. ``POST /api/onboarding/verify-bot { token }``
       Calls Telegram's ``getMe``. Returns ``{ok: true, username}`` on
       success or ``{ok: false, error}`` on failure. **Does not store**
       anything — the user has to click "Save" to commit.

    2. ``POST /api/onboarding/save-bot { token, username }``
       Writes the bot token and username into the ``settings`` table.
       Trusts the frontend that the token was verified in step 1 —
       we don't re-call Telegram here (it would cost an extra
       round-trip for no benefit). C1.1 will introduce a server-side
       check that step 1 actually ran recently.

Both endpoints are read-only or write-only against the settings table
(no semantic coupling to other tables), so they live alongside the
webui channel rather than in a future ``magi/adam/`` package.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("magi.api.onboarding")

router = APIRouter(tags=["onboarding"])

# 5s is generous for a single getMe call to Telegram's edge network.
_TELEGRAM_TIMEOUT_SECONDS = 5.0


def _state_dir() -> str:
    """Read MAGI_STATE_DIR each call — keeps state_dir testable + env-friendly."""
    return os.environ.get("MAGI_STATE_DIR", "/workspace/state")


# -- request / response schemas -----------------------------------------


class VerifyBotRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class VerifyBotResponse(BaseModel):
    ok: bool
    username: str | None = None
    error: str | None = None


class SaveBotRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=100)


class SaveBotResponse(BaseModel):
    ok: bool
    error: str | None = None


# -- endpoints ---------------------------------------------------------


@router.post("/verify-bot", response_model=VerifyBotResponse)
async def verify_bot(payload: VerifyBotRequest) -> VerifyBotResponse:
    """Verify a Telegram bot token via the official ``getMe`` call.

    Never stores anything. Returns ``{ok: true, username}`` on a real
    bot, or ``{ok: false, error}`` for any failure (network, HTTP, or
    Telegram's own ``description`` field).
    """
    token = payload.token.strip()
    if not token:
        return VerifyBotResponse(ok=False, error="Token is empty")

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        return VerifyBotResponse(ok=False, error="Telegram timed out")
    except httpx.RequestError as exc:
        return VerifyBotResponse(ok=False, error=f"Network error: {exc}")

    if resp.status_code != 200:
        return VerifyBotResponse(
            ok=False,
            error=f"Telegram returned HTTP {resp.status_code}",
        )

    data = resp.json()
    if not data.get("ok"):
        description = data.get("description", "Unknown error from Telegram")
        return VerifyBotResponse(ok=False, error=description)

    result = data.get("result") or {}
    username = result.get("username")
    if not username:
        return VerifyBotResponse(
            ok=False,
            error="Telegram response missing bot username",
        )

    return VerifyBotResponse(ok=True, username=username)


@router.post("/save-bot", response_model=SaveBotResponse)
async def save_bot(payload: SaveBotRequest) -> SaveBotResponse:
    """Persist the verified bot token + username into the settings table.

    The frontend guarantees the token passed ``verify-bot`` immediately
    before this call. Re-verifying here would cost an extra Telegram
    round-trip for no gain; the only way a stale token lands in the
    DB is if the deployer's network is hijacked between clicks.
    """
    from magi.runtime.state.settings import state_set

    state_dir = _state_dir()
    try:
        state_set(state_dir, "telegram.bot_token", payload.token)
        state_set(state_dir, "telegram.bot_username", payload.username)
    except Exception as exc:  # pragma: no cover — disk / permission errors
        logger.exception("failed to write settings")
        return SaveBotResponse(ok=False, error=str(exc))

    logger.info(
        "bot token saved",
        extra={"username": payload.username, "state_dir": state_dir},
    )
    return SaveBotResponse(ok=True)