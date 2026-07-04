"""Per-MAGI Telegram channel configuration.

Tiny settings surface for things the operator wants to tune
without redeploying: today just the read-receipt emoji, but
the same pattern (one meta key + small enum) extends to
quiet-hours / typing-indicator / etc. as those land.

Storage: ``state_set(state_dir, key, value)`` /
``state_get(state_dir, key)`` — the same key/value store
``audit_log`` already uses. The data is small (one short
string), the access pattern is read-on-every-inbound, and
we don't need schema migrations for a single emoji. C1.1
will move this into a proper ``enterprise_settings`` SQL
table; until then, the meta key is fine.

Why allowlist the emoji at the API layer rather than
free-text:

- Telegram's reaction API only accepts the ~70 standard
  ``ReactionTypeEmoji`` values (see
  :class:`telegram.constants.ReactionEmoji`). Anything else
  falls back to ``ReactionTypeCustomEmoji`` which needs a
  numeric ``custom_emoji_id`` — passing plain unicode gets
  a ``400 Bad Request: field "custom_emoji_id" must be a
  valid number`` from Telegram. The python-telegram-bot
  SDK does the routing automatically, but the allowlist
  here stops the operator from picking an emoji that
  silently fails. ``✅`` and ``💬`` look like good picks
  but aren't in Telegram's reaction whitelist.
- ``ReactionTypeCustomEmoji`` (the real thing, with a
  numeric id) requires the chat to have that emoji enabled
  — too much configuration for v0. ``ReactionTypeEmoji``
  works in every chat the bot is a member of.
- Free-text invites typos (``👀`` vs ``👁``) that look
  identical in some fonts and behave differently in
  Telegram.

The 5 emoji we ship cover "seen" / "thinking" / "ack"
signals that are common in the operator's chat UX.
"""

from __future__ import annotations

import logging

from magi.runtime.state.settings import state_get, state_set

logger = logging.getLogger("magi.channels.telegram.config")

_META_KEY = "tg.read_reaction_emoji"

# The 5 choices surfaced in the Settings UI radio group.
# Each tuple is ``(value, label)`` where ``value`` is what
# we store + send to the Telegram reaction API, and
# ``label`` is the human description under the radio row.
# Order is fixed (admin radio group iterates in this order)
# — keep it stable so a UI reorder isn't a perceived config
# change for the operator.
#
# Every ``value`` here MUST appear in
# :class:`telegram.constants.ReactionEmoji` — the python-
# telegram-bot SDK checks a string against that whitelist
# before passing it to the API. An emoji not in the
# whitelist is parsed as a ``ReactionTypeCustomEmoji``
# (which expects a numeric ``custom_emoji_id``) and the
# bot gets back ``400 Bad Request: field "custom_emoji_id"
# must be a valid number``. ``✅`` and ``💬`` look like
# great fits but Telegram doesn't ship them as reaction
# types — ``🤝`` (handshake) and ``✍`` (writing) cover
# the same semantic slots within the whitelist.
REACTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("👀", "👀  Eyes — classic 'seen' signal"),
    ("👍", "👍  Thumbs up — quick ack"),
    ("🤝", "🤝  Handshake — 'received, will handle'"),
    ("🤔", "🤔  Thinking — 'processing'"),
    ("✍", "✍  Writing — 'drafting reply'"),
)

# The default when no setting has been saved. Chosen to be
# the most universally readable ("eyes") so an operator who
# never opens Settings still gets a sensible first impression.
DEFAULT_REACTION_EMOJI = "👀"

_VALID_EMOJI: frozenset[str] = frozenset(v for v, _ in REACTION_CHOICES)


def get_read_reaction_emoji(state_dir: str) -> str:
    """Return the configured read-reaction emoji.

    Falls back to :data:`DEFAULT_REACTION_EMOJI` when:

      - no setting has ever been saved (first boot), or
      - the stored value is empty / unrecognised.

    The "unrecognised" branch matters because the meta-key
    store is just a dict — a future operator could edit it
    by hand, or an old ``custom_emoji_id`` value could leak
    in via an older code path. Returning a safe default
    keeps the inbound handler from blowing up on a bad
    string.
    """
    raw = state_get(state_dir, _META_KEY)
    if not raw:
        return DEFAULT_REACTION_EMOJI
    if raw not in _VALID_EMOJI:
        logger.warning(
            "tg.read_reaction_emoji stored value %r is not in the "
            "allowlist; falling back to default",
            raw,
        )
        return DEFAULT_REACTION_EMOJI
    return raw


def set_read_reaction_emoji(state_dir: str, emoji: str) -> None:
    """Persist a new read-reaction emoji.

    ``emoji`` must be one of :data:`REACTION_CHOICES` —
    callers (the Settings API) are responsible for the
    allowlist check before invoking this; we don't
    re-validate here because the only legitimate caller is
    the API handler and an extra check would just hide
    programming errors.
    """
    state_set(state_dir, _META_KEY, emoji)