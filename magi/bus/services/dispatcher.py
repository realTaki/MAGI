"""Bus service: dispatcher (channel routing — IM address lookup).

The dispatcher owns the per-channel IM binding map (TG chat id,
WebUI session id, etc.).  Tools and tasks look up an operator's
bound IM target for the channel they're creating on; channels
themselves are the only writers of the binding.

Channel adapters register themselves with the bus at bus bootstrap
time via :meth:`DispatcherService.register` (the bus owns the
adapter registry; :mod:`magi.channels.dispatcher` is the legacy
alias kept for channel-side code paths that need the async-send
flow).  Domain code (tools, runner, WebUI API) talks only to
``bus.dispatcher``.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ChannelAdapter(Protocol):
    """Channel-side adapter contract.

    Adapters register themselves with ``bus.dispatcher.register()``
    at bus bootstrap.  Domain code only ever calls the dispatcher;
    it never imports an adapter directly.
    """

    @property
    def name(self) -> str:
        """Stable channel id, e.g. ``"tg"`` / ``"webui"``."""
        ...

    def lookup_im_id(self, uid: int) -> Optional[str]:
        """Channel-specific IM id for ``uid`` (or ``None``)."""
        ...

    def bind_im_id(self, uid: int, im_id: str) -> None:
        """Upsert the (uid, channel=this.name) → im_id binding."""
        ...

    def unbind_im_id(self, uid: int) -> None:
        """Remove the binding for ``uid`` on this channel."""
        ...


class DispatcherService:
    """Per-channel IM routing facts; bus-owned adapter registry."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir
        self._adapters: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        """Install ``adapter`` under ``adapter.name``.

        Idempotent: re-registering the same name replaces the
        prior adapter.  Channel adapters call this at module
        import time so the bus dispatcher can resolve them.
        """
        self._adapters[adapter.name] = adapter

    def unregister(self, name: str) -> None:
        """Remove the adapter registered under ``name`` (idempotent)."""
        self._adapters.pop(name, None)

    def get_adapter(self, name: str) -> Optional[ChannelAdapter]:
        """Return the adapter registered under ``name``, or ``None``."""
        return self._adapters.get(name)

    def list_channels(self) -> list[str]:
        """Channels currently registered, in insertion order."""
        return list(self._adapters.keys())

    def lookup_im_id(self, uid: int, channel) -> Optional[str]:
        """Return the operator's bound IM id on ``channel`` or ``None``.

        Adapters register with the dispatcher at bus bootstrap;
        domain code goes through this method (never the adapter).
        Returns ``None`` when no adapter is registered for
        ``channel`` or when the user has no binding on it.
        """
        adapter = self._adapters.get(channel)
        if adapter is None:
            return None
        return adapter.lookup_im_id(uid)
