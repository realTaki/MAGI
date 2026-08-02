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
from sqlalchemy import select

from magi.bus import AgentMessage
from magi.channels.a2a.protocol import PROTOCOL_VERSION, verify_signature
from magi.db.magis import open_magis_session
from magi.db.models_magis_membership import MAGISMembership

router = APIRouter(tags=["a2a"])


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


def _same_direct_magis(sender_magic_id: int) -> bool:
    value = os.environ.get("MAGI_RUNTIME_ID")
    if not value or not value.isdigit():
        return False
    with open_magis_session() as session:
        sender = session.scalar(
            select(MAGISMembership.magis_id).where(MAGISMembership.magic_id == sender_magic_id)
        )
        own = session.scalar(
            select(MAGISMembership.magis_id).where(MAGISMembership.magic_id == int(value))
        )
    return sender is not None and sender == own


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
    if not _same_direct_magis(magic_id):
        return _error(403, "auth.out_of_scope")
    try:
        body = json.loads(raw)
        event_id = str(body["event_id"])
        text = str(body["text"])
        from_magic_id = int(body["from_magic_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _error(400, "bad_request")
    if from_magic_id != magic_id or not event_id or not text:
        return _error(400, "bad_request")

    from magi.agent.worker import submit_agent_message

    reply_to = body.get("reply_to")
    run_id = await submit_agent_message(
        AgentMessage(
            event_id=f"a2a:{magic_id}:{event_id}",
            source_id=str(magic_id),
            text=text,
            channel="a2a",
            kind="a2a.result" if reply_to else "a2a.request",
            conversation_id=f"a2a:{magic_id}:{reply_to or event_id}",
            correlation_id=str(body.get("correlation_id") or event_id),
            metadata={"from_magic_id": magic_id, "reply_to": reply_to, "a2a_event_id": event_id},
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

