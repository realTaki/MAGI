"""Narrow internal control endpoints owned by each MAGI runtime.

They are never browser-facing: the singleton WebUI reaches them with the
same target-bound HMAC used for normal runtime proxying.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.channels.webui.api.errors import MagiHTTPException
from magi.channels.webui.proxy_auth import verified_proxy_operator

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


@router.post("/control/telegram/bootstrap")
async def bootstrap_telegram(payload: TelegramBootstrap, request: Request) -> dict[str, bool]:
    _require_control(request)
    from magi.agent.db import require_state_dir
    from magi.agent.db.settings import state_set
    from magi.channels.telegram import bot as tg_bot

    state_dir = require_state_dir()
    state_set(state_dir, "telegram.bot_token", payload.token)
    state_set(state_dir, "telegram.bot_username", payload.username)
    if tg_bot.get_telegram_bot() is None:
        tg_bot.start_bot(state_dir)
    return {"ok": True}


@router.post("/control/telegram/send")
async def send_telegram(payload: TelegramSend, request: Request) -> dict[str, bool]:
    _require_control(request)
    from magi.agent.db import require_state_dir
    from magi.agent.db.settings import state_get
    from magi.channels.telegram import bot as tg_bot

    token = state_get(require_state_dir(), "telegram.bot_token")
    if not token:
        raise MagiHTTPException(status_code=409, code="telegram.not_configured", detail="Telegram is not configured")
    await tg_bot.send_text_raw(token, payload.telegram_id, payload.text)
    return {"ok": True}
