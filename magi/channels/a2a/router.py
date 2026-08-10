"""A2A ingress router.

**STUB**: A2A protocol is not designed yet. Every entry point raises
``NotImplementedError`` so callers fail fast and clearly.  The full
envelope handling from previous iterations (request → ChatJob publish,
result → SendA2AResult submit) lives below as commented-out code so the
shape is preserved when A2A work resumes.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from magi.channels.api.dependencies import get_bus

router = APIRouter(tags=["a2a"])


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


@router.post("/a2a/inbox", status_code=501)
async def receive(request: Request) -> JSONResponse:
    """A2A protocol is a stub — see module docstring."""
    raise NotImplementedError("A2A protocol is stub")


# ---------------------------------------------------------------------------
# Previous full implementation, kept commented out for future reference.
# Restoring this requires (a) finishing the A2A protocol design, (b) re-
# introducing ``run_id`` only if a real cross-MAGI correlation key is
# needed, and (c) re-routing through ``bus.agent_job_board`` after the
# ``event_id`` → ``job_id`` rename in chatJob.py.
# ---------------------------------------------------------------------------
#
# import json
# import os
# from datetime import datetime, timezone
#
# from magi.channels.a2a.protocol import PROTOCOL_VERSION, verify_signature
#
# def _can_receive_from(bus, sender_magi_id: int) -> bool:
#     if bus.memberships_book is not None:
#         try:
#             return bus.memberships_book.can_receive_a2a(sender_magi_id)
#         except (AttributeError, NotImplementedError):
#             pass
#     return True
#
# async def _receive_impl(request: Request) -> JSONResponse:
#     bus = get_bus(request)
#     raw = await request.body()
#     headers = request.headers
#     try:
#         magi_id = int(headers["x-magi-id"])
#         timestamp = int(headers["x-magi-timestamp"])
#         signature = headers["x-magi-signature"]
#     except (KeyError, ValueError):
#         return _error(400, "bad_request")
#     if headers.get("x-magi-protocol") != PROTOCOL_VERSION:
#         return _error(400, "bad_request")
#     if not verify_signature(magi_id=magi_id, timestamp=timestamp, body=raw, signature=signature):
#         return _error(401, "auth.invalid_signature")
#     if not _can_receive_from(bus, magi_id):
#         return _error(403, "auth.out_of_scope")
#     try:
#         body = json.loads(raw)
#         event_id = str(body["event_id"])
#         text = str(body["text"])
#         from_magi_id = int(body["from_magi_id"])
#         kind = str(body.get("kind") or "request")
#     except (KeyError, TypeError, ValueError, json.JSONDecodeError):
#         return _error(400, "bad_request")
#     if from_magi_id != magi_id or not event_id or not text:
#         return _error(400, "bad_request")
#     reply_to = body.get("reply_to")
#     if kind == "result":
#         if not isinstance(reply_to, str) or not reply_to:
#             return _error(400, "bad_request")
#         is_error = bool(body.get("is_error", False))
#         from magi.bus.guild.sendA2AJob import SendA2AResult
#         bus.a2a_job_board.submit_result(
#             key=reply_to,
#             result=SendA2AResult(
#                 invocation_id=reply_to,
#                 success=not is_error,
#                 status="completed" if not is_error else "failed",
#                 response={"text": text},
#                 error=text if is_error else "",
#             ),
#         )
#         return JSONResponse(
#             status_code=202,
#             content={"accepted": True, "event_id": event_id, "run_id": reply_to},
#         )
#     if kind != "request":
#         return _error(400, "bad_request")
#     from magi.bus.guild.chatJob import ChatJob
#     run_id = bus.agent_job_board.publish(
#         ChatJob(
#             job_id=f"a2a:{magi_id}:{event_id}",
#             conversation_id=f"a2a:{magi_id}:{reply_to or event_id}",
#             correlation_id=str(body.get("correlation_id") or event_id),
#             kind="a2a.request",
#             payload={
#                 "text": text,
#                 "channel": "a2a",
#                 "from_magi_id": magi_id,
#                 "reply_to": reply_to,
#                 "expect_reply": bool(reply_to),
#                 "a2a_event_id": event_id,
#             },
#         )
#     )
#     return JSONResponse(
#         status_code=202,
#         content={
#             "accepted": True,
#             "event_id": event_id,
#             "run_id": run_id,
#             "received_at": datetime.now(timezone.utc).isoformat(),
#         },
#     )
