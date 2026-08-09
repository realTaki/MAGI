"""Per-MAGI Telegram channel configuration — pure bus."""

from __future__ import annotations

import logging

from magi.channels import get_current_bus

logger = logging.getLogger("magi.channels.telegram.config")

_READ_META_KEY = "tg.read_reaction_emoji"
_DONE_META_KEY = "tg.done_reaction_emoji"

REACTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("👀", "👀  Eyes — classic 'seen' signal"),
    ("👍", "👍  Thumbs up — quick ack"),
    ("🤝", "🤝  Handshake — 'received, will handle'"),
    ("🤔", "🤔  Thinking — 'processing'"),
    ("✍", "✍  Writing — 'drafting reply'"),
    ("🏆", "🏆  Trophy — 'task complete'"),
    ("💯", "💯  100 points — 'nailed it'"),
    ("👏", "👏  Clapping — 'well done'"),
    ("🫡", "🫡  Saluting — 'mission accomplished'"),
    ("🍾", "🍾  Popping cork — 'celebration'"),
)

DEFAULT_READ_REACTION_EMOJI = "👀"
DEFAULT_DONE_REACTION_EMOJI = "🏆"

_VALID_EMOJI: frozenset[str] = frozenset(v for v, _ in REACTION_CHOICES)


def _settings():
    nb = get_current_bus()
    if nb is None:
        raise RuntimeError("bus unavailable")
    return nb.settings_book


def get_read_reaction_emoji() -> str:
    raw = _settings().get(key=_READ_META_KEY)
    if not raw or raw not in _VALID_EMOJI:
        if raw:
            logger.warning("tg.read_reaction_emoji %r not in allowlist; defaulting", raw)
        return DEFAULT_READ_REACTION_EMOJI
    return raw


def set_read_reaction_emoji(emoji: str) -> None:
    _settings().set(key=_READ_META_KEY, value=emoji)


def get_done_reaction_emoji() -> str:
    raw = _settings().get(key=_DONE_META_KEY)
    if not raw or raw not in _VALID_EMOJI:
        if raw:
            logger.warning("tg.done_reaction_emoji %r not in allowlist; defaulting", raw)
        return DEFAULT_DONE_REACTION_EMOJI
    return raw


def set_done_reaction_emoji(emoji: str) -> None:
    _settings().set(key=_DONE_META_KEY, value=emoji)
