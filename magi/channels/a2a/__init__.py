"""Agent-to-Agent (A2A) channel — MAGI peers exchange messages.

DESIGN (this file is the canonical design doc; everything else is
scaffolding around the dispatcher contract).

Goal
----
Let any MAGI push a message to a peer MAGI the same way it pushes a
message to a human user over Telegram: by routing through the channel
dispatcher (``magi.channels.dispatcher.send_to_uid``). The dispatcher
stays the single dispatch point for outbound; the a2a adapter is the
"channel" that knows how to deliver to a peer runtime instead of to
an IM endpoint.

Identity
--------
- The "uid" in the dispatcher contract is the **target MAGIC's id**
  (the `magic.id` PK on the public MAGIS PostgreSQL). Human channels
  use ``Contact.id`` (private SQLite); a2a uses ``magic.id`` (public PG)
  because the recipient is another runtime, not a person.
- The "im_id" is the peer's **k8s cluster DNS name**, e.g.
  ``magi-magi-node-7f8bcff557-5s765.magi.svc.cluster.local:42069`` or
  the cluster-local short form ``magi-magi-node-7f8bcff557-5s765:42069``.
  The dispatcher will pass it through as opaque string-typed "address".

Transport
---------
HTTP + JSON. Each MAGI container exposes a single FastAPI surface
already (``magi.channels.api``); the a2a adapter reuses the
**private runtime API** mount on that surface. Three endpoints:

  POST /a2a/inbox
      Body: ``{"from_magic_id": int, "text": str, "ts": iso}``
      The peer's runtime ingests this as if it were a user message
      arriving via the inbound queue: it goes through the agent
      loop, the reply (if any) is captured and sent back to the
      caller as the HTTP response body.

  POST /a2a/send/{magic_id}
      Outbound dial. ``send_to_uid(target_magic_id, Channel.A2A, text)``
      hits this on the *target* MAGI via the dispatcher's adapter
      contract (``adapter.send(uid, text)`` → HTTP POST).

  GET /a2a/peers
      Lists the MAGIC rows that share a direct MAGIS membership
      with the caller, optionally filtered by role. Used by the
      operator dashboard to pick a peer to address.

Authentication
--------------
Same HMAC scheme as ``magi-orchestrator``:

  ``X-MAGI-Magic-Id: <caller_magic_id>``
  ``X-MAGI-Timestamp: <unix_seconds>``
  ``X-MAGI-Signature: hex(hmac_sha256(MAGI_CONTROL_SECRET, "<id>:<ts>:<body_sha256>"))[:32]``

The shared secret is the **same** ``MAGI_CONTROL_SECRET`` that
``magi-orchestrator`` already uses for lifecycle RPCs. No new key
material is introduced — operators already know how to rotate it.

Body digest binds the signature to the exact bytes sent; replay
window is the same 5-minute clock-skew tolerance orchestrator uses.

Scope (who can send to whom)
---------------------------
The default scope is **same-MAGIS peers only**:

  - If ``caller_magic_id`` and ``target_magic_id`` share a direct
    ``MAGISMembership`` row (via their ``magis_id``), the call is
    allowed.
  - Cross-MAGIS delegation goes through the existing
    ``magi-orchestrator`` request lifecycle, not through a2a.
    a2a is a peer-runtime bus, not an organisational control
    surface.
  - ADAM-of-an-ancestor-MAGIS can speak to descendants of that
    MAGIS (mirrors the existing ``adam_manages_magis`` semantics).
    The peer list builder walks the MAGIS tree.

Identity payload (e.g. ``from_magic_id``) is **trusted** only after
the HMAC verifies against ``MAGI_CONTROL_SECRET``; before verification,
treat the request as anonymous and reject. This avoids a peer
impersonating another peer by rewriting the body.

Deferred work (this PR is **skeleton only**)
-------------------------------------------
- Pydantic models for the wire format (`A2AInboxRequest`,
  `A2ASendRequest`, `A2APeersResponse`).
- FastAPI router under the existing ``create_app(internal_routes=True)``
  path; reuse the runtime-API mounting so each MAGI process serves
  both its own runtime + the a2a surface.
- The dispatcher's ``send_to_uid`` already calls
  ``adapter.send(uid, text)``; the a2a adapter will need a
  per-target HTTP client (cached per ``magic_id``, refreshed when
  ``MAGIS_DATABASE_URL`` membership changes).
- A small rate limiter on ``/a2a/inbox`` to prevent a peer from
  flooding the local runtime.
- Wire-format version negotiation (``X-MAGI-Protocol: 1``) so the
  contract can evolve without breaking deployed peers.
- WebUI surface to inspect the peer's recent a2a messages (the
  chat pane reuses the existing ``session`` table — outbound a2a
  messages are stored as a regular session owned by the
  ``uid = magic_id`` recipient).

Adapter contract
----------------
This PR registers a stub ``A2AAdapter`` with the dispatcher so
``send_to_uid(target_magic_id, Channel.A2A, text)`` already routes
through the a2a adapter. Every method raises ``NotImplementedError``
until the runtime lands. The dispatcher contract:

  - ``name == "a2a"``
  - ``send(uid=magic_id, text)``     → POST to peer's inbox
  - ``lookup_im_id(uid=magic_id)``    → cluster DNS for the peer
  - ``bind_im_id(uid, im_id)``        → not applicable (peers
    resolve via the public PG `magic` table; no per-peer im_id
    binding needed). Raises ``NotImplementedError``.
  - ``unbind_im_id(uid)``             → not applicable. Same as above.

The import side-effect ``register_adapter(A2AAdapter())`` is what
makes ``send_to_uid`` route to the a2a adapter for ``Channel.A2A``
sessions — same pattern as ``channels/telegram/__init__.py``.
"""

from __future__ import annotations

from magi.channels.a2a.adapter import A2AAdapter
from magi.channels.dispatcher import register_adapter

# Side-effect: register the A2A adapter into the dispatcher.
# Importing this package is enough to make
# ``from magi.channels import dispatcher;
#  dispatcher.send_to_uid(magic_id, Channel.A2A, text)``
# route through the A2A adapter. The adapter itself raises
# ``NotImplementedError`` until the runtime lands — see the
# module docstring above for the design and deferred work.
register_adapter(A2AAdapter())


__all__ = ["A2AAdapter"]