"""Channel dispatcher — D.28.

The single dispatch point for "send a message to a user via a
channel" and "look up a user's IM id for a channel". Domain
code (tools, runner, webui api auth) talks to this dispatcher
only; it never imports a specific channel adapter or knows
about TG chat ids / Slack mids / etc.

Architecture (see ``docs/D.28-channel-dispatcher.md``):

    ┌──────────────────────────────────────────────────────────┐
    │  domain code (tools, runner, webui api auth, chat send) │
    │   talks in: uid + channel + session_id                  │
    └─────────────────────────┬────────────────────────────────┘
                              │
                              ▼
                   channels/dispatcher.py   ← THIS MODULE
                              │
                              ▼
       ┌──────────────────┬────────────┬────────────────┐
       ▼                  ▼            ▼                ▼
   channels/telegram  channels/slack  channels/wechat  ...
   (owns tgid)         (owns mid)    (owns wid)

Each adapter implements :class:`ChannelAdapter`. Adding a new
channel = writing one adapter + registering it. Domain code
never grows.

The dispatcher is a process-global singleton — adapters
register themselves at import time (see ``channels/telegram/
__init__.py``). Tests can swap adapters by replacing the
registry entries.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound

from magi.agent.db import open_session
from magi.agent.db.models_user_im_binding import UserImBinding

logger = logging.getLogger("magi.channels.dispatcher")


# -- Adapter protocol --------------------------------------------------------


@runtime_checkable
class ChannelAdapter(Protocol):
    """A channel adapter speaks one IM channel (TG / Slack / ...).

    Adapters are stateless aside from any bot-token / OAuth-token
    they cache at boot. The dispatcher calls into them; domain
    code only ever talks to the dispatcher.
    """

    @property
    def name(self) -> str:
        """Channel id, e.g. ``"telegram"`` / ``"slack"``.

        Used as the registry key in :data:`_ADAPTERS`. Must
        be stable across releases — the wizard / data
        binding code stores bindings keyed on this string.
        """
        ...

    async def send(self, uid: int, text: str) -> None:
        """Push a message to ``uid`` via this channel.

        The adapter resolves the bound ``im_id`` for this
        user + channel and routes through the channel's
        client (TG bot API, Slack web API, etc.). Domain
        code never touches the im_id directly.
        """
        ...

    def lookup_im_id(self, uid: int) -> str | None:
        """Return the channel-specific IM id for ``uid``,
        or ``None`` when the user has no binding.

        Domain code that needs the raw value (e.g. the
        wizard showing the bound chat id) goes through this
        method. Other code should stay at the dispatcher's
        higher-level API.
        """
        ...

    def bind_im_id(self, uid: int, im_id: str) -> None:
        """Upsert the (uid, channel=this.name) → im_id row
        in :class:`UserImBinding`.

        Called by the wizard's verify-and-bind flow when
        the user proves ownership of the IM endpoint.
        """
        ...

    def unbind_im_id(self, uid: int) -> None:
        """Remove the binding for ``uid`` on this channel.

        Idempotent: deleting a non-existent binding is a
        no-op success. The dispatcher calls this when an
        operator removes a user.
        """
        ...


# -- Adapter registry --------------------------------------------------------


_ADAPTERS: dict[str, ChannelAdapter] = {}


def register_adapter(adapter: ChannelAdapter) -> None:
    """Install ``adapter`` under ``adapter.name``.

    Idempotent: re-registering the same name replaces the
    prior adapter. Adapters call this at module import time
    (see ``channels/telegram/__init__.py``).
    """
    _ADAPTERS[adapter.name] = adapter


def get_adapter(name: str) -> ChannelAdapter | None:
    """Return the adapter registered under ``name``,
    or ``None`` if no adapter is registered for that
    channel.
    """
    return _ADAPTERS.get(name)


def list_channels() -> list[str]:
    """The channels currently registered.

    Returned in registration order (stable across a process;
    not guaranteed across restarts). Useful for the
    dashboard / wizard "what channels does MAGI support?"
    dropdown.
    """
    return list(_ADAPTERS.keys())


# -- High-level API used by domain code ---------------------------------------


async def send_to_uid(uid: int, channel: str, text: str) -> None:
    """Send ``text`` to ``uid`` via ``channel``.

    The dispatcher resolves the bound IM id (via the
    adapter's ``lookup_im_id``) and pushes. Domain code
    never sees the IM id.

    Raises:
      - ``KeyError`` if no adapter is registered for
        ``channel`` (caller passed an unknown channel).
      - ``RuntimeError`` if the user has no binding on
        ``channel`` (so the adapter has nothing to send
        to). Surfaces as a clear error rather than silent
        drop — domain code that hits this case is usually
        missing a setup step the wizard should have run.
    """
    adapter = _ADAPTERS.get(channel)
    if adapter is None:
        raise KeyError(f"no adapter registered for channel={channel!r}")
    if adapter.lookup_im_id(uid) is None:
        raise RuntimeError(
            f"user {uid} has no {channel!r} binding"
        )
    await adapter.send(uid, text)


async def send_to_session(session_id: str, text: str) -> None:
    """Send ``text`` to the session's owner via the
    session's channel.

    Used by the agent's ``send_message`` tool (D.16): the
    LLM's "side-channel" push reaches the right channel
    without the tool needing to know which one.

    Loads the session row's ``channel`` column and the
    row's ``delivery_address``, hands the address to the
    adapter (which already knows how to interpret it for
    its channel). The dispatcher itself never reads
    ``delivery_address``; that's the adapter's job.
    """
    from magi.agent.memory.session.tables import ChatSession
    with open_session() as db:
        sess = db.get(ChatSession, session_id)
    if sess is None:
        raise KeyError(f"no session {session_id}")
    adapter = _ADAPTERS.get(sess.channel)
    if adapter is None:
        raise KeyError(
            f"session {session_id} has channel={sess.channel!r} "
            f"but no adapter is registered for that channel"
        )
    await adapter.send(sess.uid, text)


def lookup_im_id(uid: int, channel: str) -> str | None:
    """Return the channel-specific IM id for ``uid``, or
    ``None`` when no binding exists.

    Convenience wrapper around ``adapter.lookup_im_id`` for
    callers that only need the value (e.g. the dashboard's
    "your binding" display).
    """
    adapter = _ADAPTERS.get(channel)
    if adapter is None:
        return None
    return adapter.lookup_im_id(uid)


def bind_im_id(uid: int, channel: str, im_id: str) -> None:
    """Upsert the (uid, channel) → im_id row.

    Convenience wrapper for the wizard's verify-and-bind
    flow. Delegates to the channel-specific adapter so
    each channel can validate the im_id format (TG chat id
    must be an int; Slack mid has its own format; etc.).
    """
    adapter = _ADAPTERS.get(channel)
    if adapter is None:
        raise KeyError(f"no adapter registered for channel={channel!r}")
    adapter.bind_im_id(uid, im_id)


def list_bindings(uid: int) -> list[tuple[str, str]]:
    """All (channel, im_id) pairs bound to ``uid``.

    Ordered by channel name. Returns ``[]`` when the user
    has no bindings. Used by the dashboard's "your
    accounts" view.
    """
    with open_session() as db:
        rows = db.scalars(
            select(UserImBinding).where(UserImBinding.uid == uid)
        ).all()
    # Defensive: if the same uid has duplicate (uid, channel)
    # rows somehow (shouldn't happen — UNIQUE constraint),
    # surface them in stable order.
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for r in sorted(rows, key=lambda r: (r.channel, r.im_id)):
        key = (r.channel, r.im_id)
        if key not in seen:
            seen.add(key)
            pairs.append(key)
    return pairs


__all__ = [
    "ChannelAdapter",
    "register_adapter",
    "get_adapter",
    "list_channels",
    "send_to_uid",
    "send_to_session",
    "lookup_im_id",
    "bind_im_id",
    "list_bindings",
]
