"""Channel dispatcher — D.28.

The single dispatch point for "send a message to a user via a
channel" and "look up a user's IM id for a channel". Domain
code (tools, runner, webui api auth) talks to this dispatcher
only; it never imports a specific channel adapter or knows
about TG chat ids / Slack mids / etc.

Architecture (see ``docs/ROADMAP.md`` §D.28):

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

from magi.bus import get_bus
from magi.channels import Channel

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
_AUTO_REGISTER_DONE = False


def register_adapter(adapter: ChannelAdapter) -> None:
    """Install ``adapter`` under ``adapter.name``.

    Idempotent: re-registering the same name replaces the
    prior adapter. Adapters call this at module import time
    (see ``channels/telegram/__init__.py``).

    Also pushes into the bus dispatcher registry so domain code
    that calls ``bus.dispatcher.lookup_im_id(...)`` sees the
    same set of adapters. The bus is the single source of
    truth for IM-binding lookup; this module keeps the legacy
    channel-side registry for the async-send / hook flow that
    only channel adapters participate in.
    """
    _ADAPTERS[adapter.name] = adapter
    state_dir = STATE_DIR
    try:
        bus = get_bus()
        bus.dispatcher.register(adapter)
    except Exception:
        # Bootstrap may not be ready yet during early module
        # import; the bus is registered lazily on first
        # ``bus.dispatcher.lookup_im_id`` call too. Avoid
        # crashing the adapter's import on a transient state.
        logger.debug("bus dispatcher registration deferred", exc_info=True)


def _auto_register_builtin_adapters() -> None:
    """Idempotent first-time import of built-in channel adapters.

    The dispatcher can't import the channel adapter packages
    at module load — that would be a circular import (the
    adapter imports ``ChannelAdapter`` from this module).
    So adapters register themselves when their *own*
    modules are first imported, and we trigger that import
    lazily from inside the public dispatcher API.

    Adding a new built-in channel: import it here. External
    (non-built-in) adapters register explicitly via
    :func:`register_adapter` from wherever they're loaded.
    """
    global _AUTO_REGISTER_DONE
    if _AUTO_REGISTER_DONE:
        return
    _AUTO_REGISTER_DONE = True
    # Each import below has a side effect: the channel's
    # __init__.py calls register_adapter() at module load.
    from magi.channels.telegram import (  # noqa: F401
        TelegramAdapter,  # noqa: F401
    )


def get_adapter(name: str) -> ChannelAdapter | None:
    """Return the adapter registered under ``name``,
    or ``None`` if no adapter is registered for that
    channel.
    """
    _auto_register_builtin_adapters()
    return _ADAPTERS.get(name)


def list_channels() -> list[str]:
    """The channels currently registered.

    Returned in registration order (stable across a process;
    not guaranteed across restarts). Useful for the
    dashboard / wizard "what channels does MAGI support?"
    dropdown.
    """
    _auto_register_builtin_adapters()
    return list(_ADAPTERS.keys())


# -- High-level API used by domain code ---------------------------------------


async def send_to_uid(uid: int, channel: Channel | str, text: str) -> None:
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
    from magi.plugins.base import Hook, PluginContext
    from magi.plugins.bus import emit

    _auto_register_builtin_adapters()
    adapter = _ADAPTERS.get(channel)
    if adapter is None:
        raise KeyError(f"no adapter registered for channel={channel!r}")
    if adapter.lookup_im_id(uid) is None:
        raise RuntimeError(
            f"user {uid} has no {channel!r} binding"
        )

    emit(
        Hook.BEFORE_CHANNEL_SEND,
        PluginContext(
            hook=Hook.BEFORE_CHANNEL_SEND,
            channel=str(channel),
            channel_target_uid=uid,
            channel_text=text,
        ),
    )

    error: str | None = None
    try:
        await adapter.send(uid, text)
    except Exception as exc:
        error = repr(exc)
        emit(
            Hook.AFTER_CHANNEL_SEND,
            PluginContext(
                hook=Hook.AFTER_CHANNEL_SEND,
                channel=str(channel),
                channel_target_uid=uid,
                channel_text=text,
                channel_error=error,
            ),
        )
        raise
    else:
        emit(
            Hook.AFTER_CHANNEL_SEND,
            PluginContext(
                hook=Hook.AFTER_CHANNEL_SEND,
                channel=str(channel),
                channel_target_uid=uid,
                channel_text=text,
            ),
        )


def lookup_im_id(uid: int, channel: Channel | str) -> str | None:
    """Return the channel-specific IM id for ``uid``, or
    ``None`` when no binding exists.

    Convenience wrapper around ``adapter.lookup_im_id`` for
    callers that only need the value (e.g. the dashboard's
    "your binding" display).
    """
    _auto_register_builtin_adapters()
    adapter = _ADAPTERS.get(channel)
    if adapter is None:
        return None
    return adapter.lookup_im_id(uid)


def bind_im_id(uid: int, channel: Channel | str, im_id: str) -> None:
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

    Currently returns the TG binding when ``Contact.telegram_id``
    is set. Future channels (WeChat, Slack) will add their own
    columns to ``Contact`` and read from there.
    """
    contact = get_bus().contacts.get(uid)
    if contact is None or contact.telegram_id is None:
        return []
    return [("telegram", str(contact.telegram_id))]


__all__ = [
    "ChannelAdapter",
    "register_adapter",
    "get_adapter",
    "list_channels",
    "send_to_uid",
    "lookup_im_id",
    "bind_im_id",
    "list_bindings",
]
