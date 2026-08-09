"""Durable A2A ingress.

This endpoint is deliberately accept-then-process: after authentication and
the short SQLite publish transaction it returns ``202``.  It never awaits an
agent step or a tool result on the HTTP request stack.

Bus selection: prefer :data:`magi.channels.get_current_new_bus` and publish
through ``agent_job_board`` / ``a2a_job_board`` (v2.0 boards; AgentWorker
consumes the same ``agent_inbox`` table either way). Falls back to the
legacy ``magi.bus`` singleton when NewBus hasn't been wired (test /
pre-cutover environments).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from magi.channels.a2a.protocol import PROTOCOL_VERSION, verify_signature
from magi.channels import get_current_new_bus

router = APIRouter(tags=["a2a"])


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


def _can_receive_from(sender_magic_id: int) -> bool:
    # NewBus memberships_book is the source of truth for A2A scope
    bus = get_current_new_bus()
    if bus is not None and bus.memberships_book is not None:
        try:
            return bus.memberships_book.can_receive_a2a(sender_magic_id)
        except (AttributeError, NotImplementedError):
            pass
    # Fallback: accept during transition
    return True


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

    if kind == "result":
        if not isinstance(reply_to, str) or not reply_to:
            return _error(400, "bad_request")
        is_error = bool(body.get("is_error", False))
        bus = get_current_new_bus()
        if bus is None:
            return _error(503, "new_bus_unavailable")
        from magi.new_bus.guild.sendA2AJob import SendA2AResult

        bus.a2a_job_board.submit_result(
            key=reply_to,
            result=SendA2AResult(
                invocation_id=reply_to,
                success=not is_error,
                status="completed" if not is_error else "failed",
                response={"text": text},
                error=text if is_error else "",
            ),
        )
        return JSONResponse(
            status_code=202,
            content={"accepted": True, "event_id": event_id, "run_id": reply_to},
        )
    if kind != "request":
        return _error(400, "bad_request")

    # v2.0 ChatJob envelope: event_id is the producer idempotency key;
    # conversation_id scopes the steering claim; payload carries all
    # channel-specific fields.
    bus = get_current_new_bus()
    if bus is None:
        return _error(503, "new_bus_unavailable")
    from magi.new_bus.guild.chatJob import ChatJob

    run_id = bus.agent_job_board.publish(
        ChatJob(
            event_id=f"a2a:{magic_id}:{event_id}",
            run_id=f"a2a:{magic_id}:{event_id}",
            conversation_id=f"a2a:{magic_id}:{reply_to or event_id}",
            correlation_id=str(body.get("correlation_id") or event_id),
            kind="a2a.request",
            payload={
                "text": text,
                "channel": "a2a",
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
