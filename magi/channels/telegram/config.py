"""Per-MAGI Telegram channel configuration.

Tiny settings surface for things the operator wants to tune
without redeploying: today the read-receipt and
done-reaction emojis, but the same pattern (one meta key +
small enum) extends to quiet-hours / typing-indicator /
etc. as those land.

Two reactions, one each side of the LLM call
--------------------------------------------
``tg.read_reaction_emoji`` is set on the user's inbound
message **before** the LLM runs (the "I've seen this and
I'm working on it" signal). Once ``handle_message`` returns
and the reply is posted, we re-set the reaction on the
**same** message using ``tg.done_reaction_emoji`` — TG's
bot reaction API replaces any prior reaction from the same
bot on the same message, so the user sees the read-receipt
get "upgraded" to done when the reply lands. No state to
keep — the API call is idempotent.

Storage: ``state_set(state_dir, key, value)`` /
``state_get(state_dir, key)``. The data is small (one short
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
  a ``400 Bad Request: field "custom_emoji_id" must be
  a valid number`` from Telegram. The python-telegram-bot
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

The allowlist covers both "seen / thinking" signals
(read side) and "done / ack" signals (done side) — the
Settings UI shows them as two separate radio groups but
they share the same underlying whitelist because Telegram
does.
"""

from __future__ import annotations

import logging

from magi.agent.db.settings import state_get, state_set

logger = logging.getLogger("magi.channels.telegram.config")

#: Meta key for the emoji we set **before** the LLM runs
#: ("I've seen this and I'm working on it").
_READ_META_KEY = "tg.read_reaction_emoji"

#: Meta key for the emoji we set **after** the reply lands
#: (overwrites the read reaction on the same message —
#: Telegram dedupes bot reactions per chat+message).
_DONE_META_KEY = "tg.done_reaction_emoji"

# The choices surfaced in the Settings UI radio group.
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
# types — ``🏆`` (trophy) and ``💯`` (100 points) cover
# the same "task done" semantic slot within the whitelist.
REACTION_CHOICES: tuple[tuple[str, str], ...] = (
    # "seen / thinking" — natural pick for the read-receipt side.
    ("👀", "👀  Eyes — classic 'seen' signal"),
    ("👍", "👍  Thumbs up — quick ack"),
    ("🤝", "🤝  Handshake — 'received, will handle'"),
    ("🤔", "🤔  Thinking — 'processing'"),
    ("✍", "✍  Writing — 'drafting reply'"),
    # "done / success" — natural pick for the done-reaction side.
    ("🏆", "🏆  Trophy — 'task complete'"),
    ("💯", "💯  100 points — 'nailed it'"),
    ("👏", "👏  Clapping — 'well done'"),
    ("🫡", "🫡  Saluting — 'mission accomplished'"),
    ("🍾", "🍾  Popping cork — 'celebration'"),
)

# Defaults. Chosen so an operator who never opens Settings
# still gets a sensible first impression: eyes on receipt
# (universally readable "seen"), trophy on done (the most
# "task complete" feeling in the whitelist — ``✅`` is not
# available there).
DEFAULT_READ_REACTION_EMOJI = "👀"
DEFAULT_DONE_REACTION_EMOJI = "🏆"

_VALID_EMOJI: frozenset[str] = frozenset(v for v, _ in REACTION_CHOICES)


def get_read_reaction_emoji(state_dir: str) -> str:
    """Return the configured read-reaction emoji.

    Falls back to :data:`DEFAULT_READ_REACTION_EMOJI` when:

      - no setting has ever been saved (first boot), or
      - the stored value is empty / unrecognised.

    The "unrecognised" branch matters because the meta-key
    store is just a dict — a future operator could edit it
    by hand, or an old ``custom_emoji_id`` value could leak
    in via an older code path. Returning a safe default
    keeps the inbound handler from blowing up on a bad
    string.
    """
    raw = state_get(state_dir, _READ_META_KEY)
    if not raw:
        return DEFAULT_READ_REACTION_EMOJI
    if raw not in _VALID_EMOJI:
        logger.warning(
            "tg.read_reaction_emoji stored value %r is not in the "
            "allowlist; falling back to default",
            raw,
        )
        return DEFAULT_READ_REACTION_EMOJI
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
    state_set(state_dir, _READ_META_KEY, emoji)


def get_done_reaction_emoji(state_dir: str) -> str:
    """Return the configured done-reaction emoji.

    Same fallback semantics as
    :func:`get_read_reaction_emoji` — see that function's
    docstring for the "unrecognised value" branch rationale.
    The key difference: this emoji is set on the user's
    inbound message **after** the LLM reply lands, so the
    user sees the read-receipt get upgraded to "done" the
    moment the assistant's text appears. Telegram's bot
    reaction API replaces any prior bot reaction on the
    same message, so the two states don't conflict.
    """
    raw = state_get(state_dir, _DONE_META_KEY)
    if not raw:
        return DEFAULT_DONE_REACTION_EMOJI
    if raw not in _VALID_EMOJI:
        logger.warning(
            "tg.done_reaction_emoji stored value %r is not in the "
            "allowlist; falling back to default",
            raw,
        )
        return DEFAULT_DONE_REACTION_EMOJI
    return raw


def set_done_reaction_emoji(state_dir: str, emoji: str) -> None:
    """Persist a new done-reaction emoji.

    Same allowlist contract as
    :func:`set_read_reaction_emoji`.
    """
    state_set(state_dir, _DONE_META_KEY, emoji)