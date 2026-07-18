"""Settings API for per-MAGI Telegram configuration.

Today this surface is the two reaction emojis the bot
sets on the user's inbound message:

- ``/api/tg-settings/read-reaction`` — the emoji we set
  **before** the LLM runs ("I've seen this").
- ``/api/tg-settings/done-reaction`` — the emoji we set
  **after** the reply lands (overwrites the read reaction;
  Telegram dedupes bot reactions per chat+message).

Future channel-level toggles (typing indicator, quiet-hours
reply, etc.) land here in the same shape.

Both handlers are admin-only (``AdminGate``). The deployed
node's TG channel reads the same values via
:func:`magi.channels.telegram.config.get_read_reaction_emoji`
/ :func:`magi.channels.telegram.config.get_done_reaction_emoji`
on every inbound message, so a Save in the UI takes effect
on the next message — no restart, no reload.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from magi.channels.webui.api.departments import AdminGate
from magi.agent.db.engine import require_state_dir
from magi.channels.telegram.config import (
    DEFAULT_DONE_REACTION_EMOJI,
    DEFAULT_READ_REACTION_EMOJI,
    REACTION_CHOICES,
    get_done_reaction_emoji,
    get_read_reaction_emoji,
    set_done_reaction_emoji,
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


class DoneReactionOut(BaseModel):
    """``GET /api/tg-settings/done-reaction`` response.

    Same shape as :class:`ReadReactionOut` — the two
    endpoints are symmetric. ``default`` here is
    :data:`DEFAULT_DONE_REACTION_EMOJI` (``🏆``) so the
    UI can hint "leave as-is if unsure" in the same way
    it does for the read reaction.
    """

    current: str
    default: str
    choices: list[ReactionChoice]


class DoneReactionUpdateRequest(BaseModel):
    """``PUT /api/tg-settings/done-reaction`` body."""

    emoji: str = Field(min_length=1, max_length=16)


@router.get("/tg-settings/read-reaction", response_model=ReadReactionOut)
def get_read_reaction(_admin: AdminGate) -> ReadReactionOut:
    return ReadReactionOut(
        current=get_read_reaction_emoji(_state_dir()),
        default=DEFAULT_READ_REACTION_EMOJI,
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
        default=DEFAULT_READ_REACTION_EMOJI,
        choices=[
            ReactionChoice(value=v, label=lbl)
            for v, lbl in REACTION_CHOICES
        ],
    )


@router.get("/tg-settings/done-reaction", response_model=DoneReactionOut)
def get_done_reaction(_admin: AdminGate) -> DoneReactionOut:
    """Return the configured done-reaction emoji + choices."""
    return DoneReactionOut(
        current=get_done_reaction_emoji(_state_dir()),
        default=DEFAULT_DONE_REACTION_EMOJI,
        choices=[
            ReactionChoice(value=v, label=lbl)
            for v, lbl in REACTION_CHOICES
        ],
    )


@router.put("/tg-settings/done-reaction", response_model=DoneReactionOut)
def put_done_reaction(
    payload: DoneReactionUpdateRequest,
    _admin: AdminGate,
) -> DoneReactionOut:
    """Persist a new done-reaction emoji.

    Same allowlist contract as :func:`put_read_reaction` —
    the two reactions share :data:`REACTION_CHOICES` because
    Telegram has a single reaction whitelist; an emoji that
    the bot can't actually send (e.g. ``✅``) is rejected
    here with the same 400 code so the UI can surface a
    consistent error.
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

    set_done_reaction_emoji(_state_dir(), payload.emoji)
    logger.info("tg done-reaction emoji set to %r", payload.emoji)
    return DoneReactionOut(
        current=payload.emoji,
        default=DEFAULT_DONE_REACTION_EMOJI,
        choices=[
            ReactionChoice(value=v, label=lbl)
            for v, lbl in REACTION_CHOICES
        ],
    )