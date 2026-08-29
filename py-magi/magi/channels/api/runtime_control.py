"""Narrow internal control endpoints owned by each MAGI runtime.

They are never browser-facing: the singleton WebUI reaches them with the
same target-bound HMAC used for normal runtime proxying.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.channels.api.dependencies import get_bus, get_workers
from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.proxy_auth import verified_proxy_operator

router = APIRouter(tags=["runtime-control"])


def _require_control(request: Request, bus) -> None:
    if verified_proxy_operator(bus, request) is None:
        raise MagiHTTPException(
            status_code=401, code="control.unauthorized", detail="Invalid control request"
        )


class TelegramBootstrap(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=100)


class TelegramSend(BaseModel):
    tgid: int
    text: str = Field(min_length=1, max_length=4000)


class TelegramVerify(BaseModel):
    token: str = Field(min_length=1, max_length=200)


@router.post("/control/telegram/bootstrap")
async def bootstrap_telegram(payload: TelegramBootstrap, request: Request) -> dict[str, bool]:
    bus = get_bus(request)
    _require_control(request, bus)

    bus.settings_book.set(key="telegram.bot_token", value=payload.token)
    bus.settings_book.set(key="telegram.bot_username", value=payload.username)
    # Hot-restart the TG polling worker so it picks up the newly-saved
    # token without a process restart. `stop_worker` is a no-op when
    # the worker isn't currently running, so this also covers the
    # cold-start case (token saved for the first time).
    workers = get_workers(request)
    await workers.stop_worker("tg")
    await workers.start_worker("tg")
    return {"ok": True}


@router.post("/control/telegram/verify")
async def verify_telegram(payload: TelegramVerify, request: Request) -> dict[str, object]:
    bus = get_bus(request)
    _require_control(request, bus)
    from magi.channels.telegram import bot as tg_bot

    try:
        username = await tg_bot.verify_token(payload.token)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "username": username}


@router.post("/control/telegram/send")
async def send_telegram(payload: TelegramSend, request: Request) -> dict[str, bool]:
    bus = get_bus(request)
    _require_control(request, bus)
    from magi.channels.telegram import bot as tg_bot

    token = bus.settings_book.get_value(key="telegram.bot_token")
    if not token:
        raise MagiHTTPException(
            status_code=409, code="telegram.not_configured", detail="Telegram is not configured"
        )
    try:
        await tg_bot.send_text_raw(token, payload.tgid, payload.text)
    except RuntimeError as exc:
        raise MagiHTTPException(
            status_code=502, code="telegram.send_failed", detail=str(exc)
        ) from exc
    return {"ok": True}
