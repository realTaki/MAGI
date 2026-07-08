"""Settings API for per-MAGI Telegram configuration.

Today this surface is just the read-receipt emoji
(``/api/tg-settings/read-reaction``); future channel-level
toggles (typing indicator, quiet-hours reply, etc.) land
here in the same shape.

The handler is admin-only (``AdminGate``). The deployed
node's TG channel reads the same value via
:func:`magi.channels.telegram.config.get_read_reaction_emoji`
on every inbound message, so a Save in the UI takes effect
on the next message — no restart, no reload.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from magi.channels.webui.api.departments import AdminGate
from magi.agent.db.engine import require_state_dir
from magi.channels.telegram.config import (
    DEFAULT_REACTION_EMOJI,
    REACTION_CHOICES,
    get_read_reaction_emoji,
    set_read_reaction_emoji,
)

logger = logging.getLogger("magi.api.tg_settings")

router = APIRouter(tags=["tg-settings"])


def _state_dir() -> str:
    return require_state_dir()


class ReactionChoice(BaseModel):
    """One row of the Settings radio group.

    ``value`` is what we send to the Telegram reaction API;
    ``label`` is what the operator sees under the radio
    (the emoji + a short human description).
    """

    value: str
    label: str


class ReadReactionOut(BaseModel):
    """``GET /api/tg-settings/read-reaction`` response.

    ``current`` is what the bot is reading right now
    (``get_read_reaction_emoji`` reads the meta key +
    falls back to the default). ``choices`` is the radio
    group the UI renders; the order in this list is the
    order the UI renders. ``default`` is the fallback
    used when the stored value is unset / invalid — the
    UI shows it under the radio group as a hint.
    """

    current: str
    default: str
    choices: list[ReactionChoice]


class ReadReactionUpdateRequest(BaseModel):
    """``PUT /api/tg-settings/read-reaction`` body."""

    emoji: str = Field(min_length=1, max_length=16)


@router.get("/tg-settings/read-reaction", response_model=ReadReactionOut)
def get_read_reaction(_admin: AdminGate) -> ReadReactionOut:
    return ReadReactionOut(
        current=get_read_reaction_emoji(_state_dir()),
        default=DEFAULT_REACTION_EMOJI,
        choices=[
            ReactionChoice(value=v, label=lbl)
            for v, lbl in REACTION_CHOICES
        ],
    )


@router.put("/tg-settings/read-reaction", response_model=ReadReactionOut)
def put_read_reaction(
    payload: ReadReactionUpdateRequest,
    _admin: AdminGate,
) -> ReadReactionOut:
    """Persist a new read-reaction emoji.

    Validates against :data:`REACTION_CHOICES`; an unknown
    emoji returns 400 ``validation.unknown_reaction_emoji``
    so the operator gets a clear "pick from the list" hint
    instead of a silent write that the bot then can't use.
    """
    from magi.channels.webui.api.errors import MagiHTTPException

    allowed = {v for v, _ in REACTION_CHOICES}
    if payload.emoji not in allowed:
        raise MagiHTTPException(
            status_code=400,
            code="validation.unknown_reaction_emoji",
            detail=(
                f"emoji {payload.emoji!r} is not in the allowed "
                f"set: {sorted(allowed)}"
            ),
        )

    set_read_reaction_emoji(_state_dir(), payload.emoji)
    logger.info("tg read-reaction emoji set to %r", payload.emoji)
    return ReadReactionOut(
        current=payload.emoji,
        default=DEFAULT_REACTION_EMOJI,
        choices=[
            ReactionChoice(value=v, label=lbl)
            for v, lbl in REACTION_CHOICES
        ],
    )