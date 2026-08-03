"""Durable A2A ingress.

This endpoint is deliberately accept-then-process: after authentication and
the short SQLite publish transaction it returns ``202``.  It never awaits an
agent step or a tool result on the HTTP request stack.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from magi.bus import AgentMessage, bootstrap
from magi.channels.a2a.protocol import PROTOCOL_VERSION, verify_signature

router = APIRouter(tags=["a2a"])


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


def _can_receive_from(sender_magic_id: int) -> bool:
    return bootstrap(os.environ.get("MAGI_STATE_DIR", "")).magic.can_receive_a2a(sender_magic_id)


@router.post("/a2a/inbox", status_code=202)
async def receive(request: Request) -> JSONResponse:
    raw = await request.body()
    headers = request.headers
    try:
        magic_id = int(headers["x-magi-magic-id"])
        timestamp = int(headers["x-magi-timestamp"])
        signature = headers["x-magi-signature"]
    except (KeyError, ValueError):
        return _error(400, "bad_request")
    if headers.get("x-magi-protocol") != PROTOCOL_VERSION:
        return _error(400, "bad_request")
    if not verify_signature(magic_id=magic_id, timestamp=timestamp, body=raw, signature=signature):
        return _error(401, "auth.invalid_signature")
    if not _can_receive_from(magic_id):
        return _error(403, "auth.out_of_scope")
    try:
        body = json.loads(raw)
        event_id = str(body["event_id"])
        text = str(body["text"])
        from_magic_id = int(body["from_magic_id"])
        kind = str(body.get("kind") or "request")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _error(400, "bad_request")
    if from_magic_id != magic_id or not event_id or not text:
        return _error(400, "bad_request")

    reply_to = body.get("reply_to")
    bus = bootstrap(os.environ.get("MAGI_STATE_DIR", ""))
    if kind == "result":
        if not isinstance(reply_to, str) or not reply_to:
            return _error(400, "bad_request")
        completed_run = bus.agent_runs.complete_a2a(
            reply_to=reply_to,
            content=text,
            is_error=bool(body.get("is_error", False)),
        )
        return JSONResponse(
            status_code=202,
            content={"accepted": True, "event_id": event_id, "run_id": completed_run},
        )
    if kind != "request":
        return _error(400, "bad_request")
    run_id = bus.agent_runs.publish_input(
        AgentMessage(
            event_id=f"a2a:{magic_id}:{event_id}",
            source_id=str(magic_id),
            # Cross-channel idempotency triple (0009_idempotency_keys):
            # the body-level ``event_id`` (the caller's stable id) is
            # the upstream-stable handle. A redelivered request with a
            # fresh local envelope collapses to the same inbox row.
            source_type="a2a",
            external_event_id=str(event_id),
            text=text,
            channel="a2a",
            kind="a2a.request",
            conversation_id=f"a2a:{magic_id}:{reply_to or event_id}",
            correlation_id=str(body.get("correlation_id") or event_id),
            metadata={
                "from_magic_id": magic_id,
                "reply_to": reply_to,
                "expect_reply": bool(reply_to),
                "a2a_event_id": event_id,
            },
        )
    )
    return JSONResponse(
        status_code=202,
        content={
            "accepted": True,
            "event_id": event_id,
            "run_id": run_id,
            "received_at": datetime.now(timezone.utc).isoformat(),
        },
    )
