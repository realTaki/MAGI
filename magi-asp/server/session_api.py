"""Participant session HTTP API and MAGI WebSocket protocol."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from .api_models import (
    AcknowledgeEventsBody,
    CreateSessionBody,
    InviteBody,
    ReopenBody,
    SendMessageBody,
)
from .service import Conflict, NotAllowed, NotFound, SessionService
from .store import Store
from .transport import Transport


def session_router(
    service: SessionService,
    store: Store,
    transport: Transport,
    auth_handle: Callable[[Request], str],
) -> APIRouter:
    router = APIRouter()

    # ---- Sessions ------------------------------------------------------

    # 201 Created per RFC 9110 §15.3.2: a new session resource is identified
    # by `session_id`. Lifecycle verbs (join, invite, leave, end, reopen)
    # mutate state without creating a top-level resource and stay 200.
    @router.post("/sessions", status_code=201)
    async def post_sessions(body: CreateSessionBody, request: Request):
        creator = auth_handle(request)
        if body.end_after_send and body.initial_message is None:
            raise HTTPException(
                status_code=400,
                detail="end_after_send requires initial_message",
            )
        result = await service.create_session(
            creator=creator,
            invite=body.invite,
            topic=body.topic,
            initial_message=body.initial_message.model_dump(exclude_none=True)
            if body.initial_message is not None
            else None,
            end_after_send=body.end_after_send,
            idempotency_key=body.idempotency_key,
        )
        out: dict[str, Any] = {"session_id": result.session_id}
        if result.sequence is not None:
            out["sequence"] = result.sequence
        return out

    @router.post("/sessions/{session_id}/join")
    async def post_join(session_id: str, request: Request):
        handle = auth_handle(request)
        try:
            await service.join(handle, session_id)
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        except Conflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except NotAllowed:
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @router.post("/sessions/{session_id}/invite")
    async def post_invite(session_id: str, body: InviteBody, request: Request):
        caller = auth_handle(request)
        try:
            invited = await service.invite(caller, session_id, body.invite)
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        except NotAllowed:
            raise HTTPException(status_code=404, detail="not found")
        return {"invited": invited}

    # 201 Created per RFC 9110 §15.3.2: a new message resource is identified
    # by `message_id`.
    @router.post("/sessions/{session_id}/messages", status_code=201)
    async def post_message(session_id: str, body: SendMessageBody, request: Request):
        sender = auth_handle(request)
        try:
            result = await service.send_message(
                sender,
                session_id,
                body.content,
                body.idempotency_key,
                body.metadata,
            )
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        except Conflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except NotAllowed:
            raise HTTPException(status_code=404, detail="not found")
        return {"message_id": result.message_id, "sequence": result.sequence}

    @router.post("/sessions/{session_id}/leave")
    async def post_leave(session_id: str, request: Request):
        handle = auth_handle(request)
        try:
            await service.leave(handle, session_id)
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        except Conflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except NotAllowed:
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @router.post("/sessions/{session_id}/end")
    async def post_end(session_id: str, request: Request):
        handle = auth_handle(request)
        try:
            await service.end(handle, session_id)
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        except Conflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except NotAllowed:
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @router.post("/sessions/{session_id}/reopen")
    async def post_reopen(session_id: str, body: ReopenBody, request: Request):
        handle = auth_handle(request)
        try:
            await service.reopen(
                handle,
                session_id,
                body.invite,
                body.initial_message.model_dump(exclude_none=True)
                if body.initial_message is not None
                else None,
            )
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        except Conflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except NotAllowed:
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str, request: Request):
        caller = auth_handle(request)
        try:
            return service.get_session_view(caller, session_id)
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")

    @router.get("/sessions/{session_id}/events")
    async def get_session_events(
        session_id: str,
        request: Request,
        after_sequence: int | None = None,
        limit: int | None = None,
    ):
        caller = auth_handle(request)
        try:
            events = service.get_events_for(caller, session_id, after_sequence, limit)
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        return {"events": events}

    @router.post("/sessions/{session_id}/events/ack")
    async def post_event_ack(session_id: str, body: AcknowledgeEventsBody, request: Request):
        caller = auth_handle(request)
        try:
            service.acknowledge(caller, session_id, body.event_ids)
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    # ---- WebSocket -----------------------------------------------------

    @router.websocket("/connect")
    async def ws_connect(ws: WebSocket):
        # Headers come in via the upgrade request.
        auth_header = ws.headers.get("authorization", "")
        token = auth_header.removeprefix("Bearer ") if auth_header.startswith("Bearer ") else ""
        agent = store.authenticate(token) if token else None
        if agent is None:
            await ws.close(code=1008)
            return
        await ws.accept()
        await transport.connect(agent.handle, ws)
        try:
            while True:
                message = json.loads(await ws.receive_text())
                if isinstance(message, dict):
                    if message.get("type") == "session.ack":
                        session_id = message.get("session_id")
                        event_id = message.get("event_id")
                        if isinstance(session_id, str) and isinstance(event_id, str):
                            try:
                                service.acknowledge(agent.handle, session_id, [event_id])
                            except NotFound:
                                pass
                    else:
                        transport.receive_control(agent.handle, message)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        # Schedule cleanup as a background task so the close ACK isn't
        # blocked behind session.disconnected fan-out.
        asyncio.create_task(transport.disconnect(agent.handle, ws))

    return router
