"""Bus service: dispatcher (channel routing — IM address lookup).

The dispatcher owns the per-channel IM binding map (TG chat id,
WebUI session id, etc.).  Tools and tasks look up an operator's
bound IM target for the channel they're creating on; channels
themselves are the only writers of the binding.
"""

from __future__ import annotations

from typing import Optional


class DispatcherService:
    """Per-channel IM routing facts."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def lookup_im_id(self, uid: int, channel) -> Optional[str]:
        """Return the operator's bound IM id on ``channel`` or ``None``."""
        from magi.channels.dispatcher import lookup_im_id
        return lookup_im_id(uid, channel)
