"""Narrow internal control endpoints owned by each MAGI runtime.

They are never browser-facing: the singleton WebUI reaches them with the
same target-bound HMAC used for normal runtime proxying.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.proxy_auth import verified_proxy_operator
from magi.channels.api.dependencies import get_bus

router = APIRouter(tags=["runtime-control"])

def _require_control(request: Request) -> None:
    if verified_proxy_operator(request) is None:
        raise MagiHTTPException(status_code=401, code="control.unauthorized", detail="Invalid control request")


class TelegramBootstrap(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=100)


class TelegramSend(BaseModel):
    telegram_id: int
    text: str = Field(min_length=1, max_length=4000)


class TelegramVerify(BaseModel):
    token: str = Field(min_length=1, max_length=200)


@router.post("/control/telegram/bootstrap")
async def bootstrap_telegram(payload: TelegramBootstrap, request: Request) -> dict[str, bool]:
    _require_control(request)
    from magi.channels.telegram import bot as tg_bot

    bus = get_bus(request)
    bus.settings_book.set(key="telegram.bot_token", value=payload.token)
    bus.settings_book.set(key="telegram.bot_username", value=payload.username)
    if tg_bot.get_telegram_bot() is None:
        tg_bot.start_bot()
    return {"ok": True}


@router.post("/control/telegram/verify")
async def verify_telegram(payload: TelegramVerify, request: Request) -> dict[str, object]:
    _require_control(request)
    from magi.channels.telegram import bot as tg_bot

    try:
        username = await tg_bot.verify_token(payload.token)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "username": username}


@router.post("/control/telegram/send")
async def send_telegram(payload: TelegramSend, request: Request) -> dict[str, bool]:
    _require_control(request)
    from magi.channels.telegram import bot as tg_bot

    token = get_bus(request).settings_book.get(key="telegram.bot_token")
    if not token:
        raise MagiHTTPException(status_code=409, code="telegram.not_configured", detail="Telegram is not configured")
    try:
        await tg_bot.send_text_raw(token, payload.telegram_id, payload.text)
    except RuntimeError as exc:
        raise MagiHTTPException(status_code=502, code="telegram.send_failed", detail=str(exc)) from exc
    return {"ok": True}
