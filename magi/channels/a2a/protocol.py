"""A2A wire-format protocol — constants + signing scheme.

This module is **the contract** the runtime will eventually
implement. Pydantic models are deferred (see package docstring),
so for now we keep the wire surface as documented constants
plus the HMAC signing function. Anyone implementing the
inbound ``/a2a/inbox`` or outbound ``adapter.send`` should
match these exactly; the test suite will eventually pin them
end-to-end.

Wire shape
----------

Inbound (peer → me):

    POST /a2a/inbox
    Headers:
        X-MAGI-Magic-Id:        <caller magic_id, int>
        X-MAGI-Timestamp:      <unix_seconds, int>
        X-MAGI-Signature:       hex(hmac_sha256(MAGI_CONTROL_SECRET,
                                            payload_digest)[:32])
        X-MAGI-Protocol:        "1"
    Body (JSON):
        {
          "from_magic_id": int,        # duplicate of header, integrity check
          "text":          str,         # the message body
          "ts":            iso8601,     # caller-side wall clock (audit only)
          "reply_to":      str | null   # optional correlation id
        }
    Response 200:
        {
          "received_at":   iso8601,
          "reply":         str | null   # optional same-call reply
                              # (only when caller passes
                              #  ``X-MAGI-Want-Reply: 1``)
        }
    Response 401:
        {"error": "auth.invalid_signature"}
    Response 403:
        {"error": "auth.out_of_scope"}
    Response 429:
        {"error": "rate_limited", "retry_after": int_seconds}

Outbound (me → peer):

    POST {peer_runtime_address}/a2a/inbox
    Headers: same as inbound
    Body:    same as inbound

Body digest (what HMAC signs):

    payload_digest = sha256(canonical_body_bytes).hexdigest()
    signed_string  = f"{magic_id}:{timestamp}:{payload_digest}"
    signature      = hmac_sha256(MAGI_CONTROL_SECRET, signed_string)[:32]

Replay window: 5 minutes clock-skew tolerance, same as
``magi-orchestrator``. The receiver must reject signatures with
``abs(now - timestamp) > 300``.

Error model: every non-2xx response carries ``{"error": "<code>"}``
where ``<code>`` is one of:

    auth.invalid_signature       HMAC mismatch
    auth.replayed_timestamp      outside replay window
    auth.out_of_scope            caller not in same MAGIS as target
    rate_limited                 caller exceeded ``/a2a/inbox`` quota
    bad_request                  malformed body / missing headers
    internal_error               unexpected receiver-side failure

Why these codes: domain code (tools, runner, webui) treats
non-2xx as opaque ``response.json()["error"]`` — no extra
schema to learn. The A2A worker logs the code on the sending
side for ops triage.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Final

logger = logging.getLogger("magi.channels.a2a.protocol")


#: Wire-format version. Bump when the contract changes
#: in a backwards-incompatible way; receivers MUST reject
#: unknown protocol versions with ``bad_request``.
PROTOCOL_VERSION: Final[str] = "1"

#: Replay-window tolerance (seconds). Same as
#: ``magi-orchestrator``: the runtime should reject
#: signatures whose ``timestamp`` is more than this far
#: from wall-clock now.
REPLAY_WINDOW_SECONDS: Final[int] = 300

#: HMAC signature truncation length (hex chars). 32 hex
#: chars = 128 bits — well past collision territory for
#: the use case, and short enough to keep headers tidy.
SIGNATURE_LENGTH: Final[int] = 32

#: Error codes the A2A worker logs on the sending side
#: when a peer responds with a non-2xx status.
ERROR_INVALID_SIGNATURE: Final[str] = "auth.invalid_signature"
ERROR_REPLAYED_TIMESTAMP: Final[str] = "auth.replayed_timestamp"
ERROR_OUT_OF_SCOPE: Final[str] = "auth.out_of_scope"
ERROR_RATE_LIMITED: Final[str] = "rate_limited"
ERROR_BAD_REQUEST: Final[str] = "bad_request"
ERROR_INTERNAL: Final[str] = "internal_error"


def _shared_secret() -> bytes | None:
    """Return the shared secret used to sign a2a requests.

    Reuses ``MAGI_CONTROL_SECRET`` (same as
    ``magi-orchestrator``). Operators already rotate this
    through the same Secret workflow; introducing a second
    key would split the operator's mental model.

    Returns ``None`` when the env var is unset — the
    caller (``verify_signature`` / ``sign_request``) should
    refuse to operate in that case rather than signing with
    an empty key.
    """
    secret = os.environ.get("MAGI_CONTROL_SECRET")
    if not secret:
        return None
    return secret.encode("utf-8")


def sign_request(
    *,
    magic_id: int,
    body: bytes,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Compute the headers an a2a client must send.

    Returns a dict ready to splat into ``httpx.AsyncClient.post(
    headers=...)``:

        {
          "X-MAGI-Magic-Id":   "<int>",
          "X-MAGI-Timestamp":   "<int>",
          "X-MAGI-Signature":   "<hex>",
          "X-MAGI-Protocol":    "1",
        }

    The signature covers ``"<magic_id>:<timestamp>:<sha256(body)>"``
    so the receiver can verify the body bytes have not been
    mutated in flight.

    Raises ``RuntimeError`` when ``MAGI_CONTROL_SECRET`` is
    unset — better to fail loudly at the call site than to
    silently send requests with an empty signature.
    """
    secret = _shared_secret()
    if secret is None:
        raise RuntimeError(
            "MAGI_CONTROL_SECRET is not set; cannot sign a2a request"
        )

    if timestamp is None:
        timestamp = int(time.time())
    payload_digest = hashlib.sha256(body).hexdigest()
    signed = f"{magic_id}:{timestamp}:{payload_digest}".encode("utf-8")
    signature = hmac.new(secret, signed, hashlib.sha256).hexdigest()[
        :SIGNATURE_LENGTH
    ]
    return {
        "X-MAGI-Magic-Id": str(magic_id),
        "X-MAGI-Timestamp": str(timestamp),
        "X-MAGI-Signature": signature,
        "X-MAGI-Protocol": PROTOCOL_VERSION,
    }


def verify_signature(
    *,
    magic_id: int,
    timestamp: int,
    body: bytes,
    signature: str,
    now: int | None = None,
) -> bool:
    """Verify the incoming request headers + body.

    Returns ``True`` when:

      - signature matches ``hmac_sha256(secret, "<id>:<ts>:<digest>")``;
      - ``abs(now - timestamp) <= REPLAY_WINDOW_SECONDS``;
      - signature length is exactly ``SIGNATURE_LENGTH``.

    Returns ``False`` (never raises) on any failure — the
    caller maps ``False`` to ``401 auth.invalid_signature``
    or ``401 auth.replayed_timestamp`` based on which
    precondition failed.

    Pass ``now`` to inject wall-clock for tests; production
    uses ``int(time.time())``.
    """
    secret = _shared_secret()
    if secret is None:
        logger.warning(
            "MAGI_CONTROL_SECRET unset; rejecting a2a request"
        )
        return False

    if len(signature) != SIGNATURE_LENGTH:
        return False

    if now is None:
        now = int(time.time())
    if abs(now - int(timestamp)) > REPLAY_WINDOW_SECONDS:
        return False

    payload_digest = hashlib.sha256(body).hexdigest()
    signed = f"{magic_id}:{timestamp}:{payload_digest}".encode("utf-8")
    expected = hmac.new(secret, signed, hashlib.sha256).hexdigest()[
        :SIGNATURE_LENGTH
    ]
    return hmac.compare_digest(expected, signature)


__all__ = [
    "PROTOCOL_VERSION",
    "REPLAY_WINDOW_SECONDS",
    "SIGNATURE_LENGTH",
    "ERROR_INVALID_SIGNATURE",
    "ERROR_REPLAYED_TIMESTAMP",
    "ERROR_OUT_OF_SCOPE",
    "ERROR_RATE_LIMITED",
    "ERROR_BAD_REQUEST",
    "ERROR_INTERNAL",
    "sign_request",
    "verify_signature",
]
