"""A2A peer-route resolver.

The A2A worker owns durable peer delivery. This module only resolves a
target MAGI to its runtime address; it is not a channel adapter and is not
registered in the human-channel delivery path.

Route resolver contract:

  - ``name`` is ``"a2a"`` (matches :attr:`magi.channels.Channel.A2A`).
  - ``send(contact_id=magi_id, text)``
      Resolves the peer's cluster DNS via
      ``lookup_im_id(contact_id)`` and POSTs to ``{peer}/a2a/inbox``.
      Future work; raises ``NotImplementedError`` for now.
  - ``lookup_im_id(contact_id=magi_id)``
      Returns the cluster DNS name of the peer's runtime pod,
      resolved from the public MAGIS shared database
      ``magis_memberships.id`` row + k8s convention
      ``magi-magi-node-<magi_id>-<suffix>``.
      **Implemented as a stub** — returns a synthetic
      placeholder so domain code can already serialise the
      result without crashing.
  - ``bind_im_id(contact_id, im_id)``
      Not applicable — peer routing is identity-based via the
      ``magis_memberships`` table, no per-peer im_id binding
      needed. Raises ``NotImplementedError``.
  - ``unbind_im_id(contact_id)``
      Not applicable — same as ``bind_im_id``. Raises
      ``NotImplementedError``.

Concrete FastAPI router + Pydantic models for the wire format are
scheduled in the deferred-work list in the package docstring.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from magi.channels import Channel

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.channels.a2a.adapter")


class A2AAdapter:
    """Channel adapter for MAGI-to-MAGI peer messaging.

    Holds the explicit BUS dependency. The HTTP client (when implemented) will be
    cached per peer ``magi_id`` and refreshed when the
    direct-membership list changes (the membership change is
    visible to this process via the public MAGIS shared database,
    same engine used by every other runtime path).
    """

    name: str = Channel.A2A

    def __init__(self, bus: Bus) -> None:
        self.bus = bus

    async def send(self, contact_id: int, text: str) -> None:
        """Push ``text`` to peer ``contact_id`` (a MAGI's
        ``magis_memberships.id``).

        Queue a durable A2A delivery through bus delivery_job_board.
        """
        if not text:
            raise ValueError("A2A messages cannot be empty")
        if not os.environ.get("MAGI_RUNTIME_ID", "").isdigit():
            raise RuntimeError("MAGI_RUNTIME_ID is required for A2A delivery")

        from magi.bus.guild.deliveryJob import DeliveryJob

        self.bus.delivery_job_board.publish(
            DeliveryJob(
                channel=Channel.A2A,
                destination=str(contact_id),
                payload={"text": text, "reply_to": None},
            )
        )

    def lookup_im_id(self, contact_id: int) -> str | None:
        """Return the peer's cluster DNS name, or ``None``.

        Implemented as a stub so the route-resolver contract
        resolves without raising: callers can already read
        the placeholder. The placeholder uses the
        ``magi-magi-node-<contact_id>`` prefix matching the k8s
        convention established in
        ``deploy/k8s/base/deployment.yaml`` + the
        orchestrator's runtime-resource naming. The
        ``HEADLESS_HOSTNAME`` suffix is filled in by k8s at
        runtime; the placeholder shape is what the HTTP
        client would resolve via DNS SRV lookup in
        production.

        Future work: query the public MAGIS shared database for
        the peer's ``magis_memberships.id`` row, then build the actual
        FQDN from the orchestrator-managed ``Service`` /
        ``Deployment`` name.
        """
        return f"magi-magi-node-{contact_id}.magi.svc.cluster.local:42069"

    def bind_im_id(self, contact_id: int, im_id: str) -> None:
        """Not applicable for a2a — peer routing uses the
        public ``magis_memberships`` table, no per-peer im_id
        binding.

        A2A has no user-selectable address binding. We raise explicitly so a
        future caller that tries to reuse human-channel binding gets a clear
        error.
        """
        raise NotImplementedError(
            "A2AAdapter.bind_im_id is not applicable: peer "
            "routing is identity-based via the public "
            "MAGIS shared-database magis_memberships table"
        )

    def unbind_im_id(self, contact_id: int) -> None:
        """Not applicable for a2a — see ``bind_im_id``."""
        raise NotImplementedError(
            "A2AAdapter.unbind_im_id is not applicable: peer "
            "routing is identity-based via the public "
            "MAGIS shared-database magis_memberships table"
        )


__all__ = ["A2AAdapter"]
