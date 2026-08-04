"""A2A channel adapter — skeleton.

Design lives in :mod:`magi.channels.a2a` (the package docstring).
This file owns the dispatcher contract surface; every method
that talks to the wire is intentionally ``NotImplementedError``
until the runtime lands. The adapter is registered at import
time so the dispatcher routes ``Channel.A2A`` to it from day
one (letting domain code reach ``send_to_uid`` + ``lookup_im_id``
without crashing).

Adapter contract — see ``magi.channels.dispatcher.ChannelAdapter``:

  - ``name`` is ``"a2a"`` (matches :attr:`magi.channels.Channel.A2A`).
  - ``send(uid=magic_id, text)``
      Resolves the peer's cluster DNS via
      ``lookup_im_id(uid)`` and POSTs to ``{peer}/a2a/inbox``.
      Future work; raises ``NotImplementedError`` for now.
  - ``lookup_im_id(uid=magic_id)``
      Returns the cluster DNS name of the peer's runtime pod,
      resolved from the public MAGIS PostgreSQL ``magic`` row +
      k8s convention ``magi-magi-node-<magic_id>-<suffix>``.
      **Implemented as a stub** — returns a synthetic
      placeholder so domain code can already serialise the
      result without crashing.
  - ``bind_im_id(uid, im_id)``
      Not applicable — peer routing is identity-based via the
      ``magic`` table, no per-peer im_id binding needed.
      Raises ``NotImplementedError``.
  - ``unbind_im_id(uid)``
      Not applicable — same as ``bind_im_id``. Raises
      ``NotImplementedError``.

Concrete FastAPI router + Pydantic models for the wire format are
scheduled in the deferred-work list in the package docstring.
"""

from __future__ import annotations

import logging
import os

from magi.channels import Channel

logger = logging.getLogger("magi.channels.a2a.adapter")


class A2AAdapter:
    """Channel adapter for MAGI-to-MAGI peer messaging.

    Holds no state. The HTTP client (when implemented) will be
    cached per peer ``magic_id`` and refreshed when the
    direct-membership list changes (the membership change is
    visible to this process via the public MAGIS PostgreSQL,
    same engine used by every other runtime path).
    """

    name: str = Channel.A2A

    async def send(self, uid: int, text: str) -> None:
        """Push ``text`` to peer ``uid`` (a ``magic.id``).

        Queue a durable A2A delivery.  The delivery worker owns the HTTP
        request/retry path, so callers never hold an agent stack open while a
        peer thinks.
        """
        from magi.bus import get_bus

        if not text:
            raise ValueError("A2A messages cannot be empty")
        if not os.environ.get("MAGI_RUNTIME_ID", "").isdigit():
            raise RuntimeError("MAGI_RUNTIME_ID is required for A2A delivery")
        get_bus().delivery.enqueue(
            channel=Channel.A2A,
            destination=str(uid),
            payload={"text": text, "reply_to": None},
        )

    def lookup_im_id(self, uid: int) -> str | None:
        """Return the peer's cluster DNS name, or ``None``.

        Implemented as a stub so the dispatcher contract
        resolves without raising: callers can already read
        the placeholder. The placeholder uses the
        ``magi-magi-node-<uid>`` prefix matching the k8s
        convention established in
        ``deploy/k8s/base/deployment.yaml`` + the
        orchestrator's runtime-resource naming. The
        ``HEADLESS_HOSTNAME`` suffix is filled in by k8s at
        runtime; the placeholder shape is what the HTTP
        client would resolve via DNS SRV lookup in
        production.

        Future work: query the public MAGIS PostgreSQL for
        the peer's ``magic.id`` row, then build the actual
        FQDN from the orchestrator-managed ``Service`` /
        ``Deployment`` name.
        """
        return f"magi-magi-node-{uid}.magi.svc.cluster.local:42069"

    def bind_im_id(self, uid: int, im_id: str) -> None:
        """Not applicable for a2a — peer routing uses the
        public ``magic`` table, no per-peer im_id binding.

        The dispatcher does not call this method (it is only
        invoked from the wizard's verify-and-bind flow,
        which is human-channel specific), so the runtime
        never reaches this stub in practice. We raise
        explicitly so a future caller that tries to reuse
        the bind API for a2a gets a clear error.
        """
        raise NotImplementedError(
            "A2AAdapter.bind_im_id is not applicable: peer "
            "routing is identity-based via the public "
            "MAGIS PostgreSQL magic table"
        )

    def unbind_im_id(self, uid: int) -> None:
        """Not applicable for a2a — see ``bind_im_id``."""
        raise NotImplementedError(
            "A2AAdapter.unbind_im_id is not applicable: peer "
            "routing is identity-based via the public "
            "MAGIS PostgreSQL magic table"
        )


__all__ = ["A2AAdapter"]
