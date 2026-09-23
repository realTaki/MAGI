"""FastAPI app: HTTP routes and the WS /connect endpoint."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from db.database import LocalDatabase
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import AfterValidator, BaseModel, Field

from .service import (
    Conflict,
    NotAllowed,
    NotFound,
    Service,
)
from .spawn import MagiSpawner, default_spawner
from .store import Store
from .transport import Transport

# Legacy key retained only until the app has copied and removed old settings.
PROVIDER_SETTING_KEY = "provider"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


def _require_non_empty_content(value: Any) -> Any:
    """Schema bar from `common.json#/$defs/Content`: empty payloads are
    rejected. The string form needs `minLength: 1`, the array form
    needs `minItems: 1`, and a `TextPart` with empty `text` is rejected
    by `TextPart.text`'s own `minLength: 1`.
    """
    if isinstance(value, str):
        if len(value) == 0:
            raise ValueError("content must not be empty")
    elif isinstance(value, list):
        if len(value) == 0:
            raise ValueError("content must contain at least one part")
        for part in value:
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
                and len(part["text"]) == 0
            ):
                raise ValueError("text part must not be empty")
    return value


Content = Annotated[Any, AfterValidator(_require_non_empty_content)]


class InitialMessage(BaseModel):
    content: Content
    metadata: dict | None = None


class CreateSessionBody(BaseModel):
    invite: list[str] = Field(default_factory=list)
    topic: str | None = None
    initial_message: InitialMessage | None = None
    end_after_send: bool = False
    idempotency_key: str | None = None


class InviteBody(BaseModel):
    invite: list[str]


class SendMessageBody(BaseModel):
    content: Content
    idempotency_key: str | None = None
    metadata: dict | None = None


class ReopenBody(BaseModel):
    invite: list[str] | None = None
    initial_message: InitialMessage | None = None


class CreateConversationBody(BaseModel):
    kind: Literal["bot", "group"]


class UpdateConversationBody(BaseModel):
    topic: str | None = None
    description: str | None = None


class AddMemberBody(BaseModel):
    handle: str


class UpdateNicknameBody(BaseModel):
    nickname: str


class ProviderSettingsBody(BaseModel):
    """A complete, transient provider update from the app."""

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    handles: list[str] | None = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


@dataclass
class AspOperator:
    """The ASP subsystem owned by the ASP server process."""

    router: APIRouter
    store: Store
    transport: Transport
    service: Service
    spawner: MagiSpawner
    base_url: str = "http://127.0.0.1:42069"

    async def close(self) -> None:
        self.spawner.close()
        await self.transport.close()


def create_operator(
    seed: dict[str, str],
    *,
    storage: LocalDatabase,
    spawner: MagiSpawner | None = None,
    base_url: str = "http://127.0.0.1:42069",
) -> AspOperator:
    """Create ASP routes without creating a second FastAPI application."""
    router = APIRouter()

    store = Store()
    store.seed_agents(seed)
    transport = Transport(store)
    service = Service(store, transport)
    magi_spawner = spawner if spawner is not None else default_spawner()

    # ---- Auth helper ---------------------------------------------------

    def auth_handle(request: Request) -> str:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing credentials")
        token = auth_header.removeprefix("Bearer ")
        agent = store.authenticate(token)
        if agent is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        return agent.handle

    # ---- Conversations (operator plus-button) --------------------------

    @router.post("/conversations", status_code=201)
    async def post_conversations(body: CreateConversationBody, request: Request):
        creator = auth_handle(request)
        return await service.create_conversation(
            creator=creator,
            kind=body.kind,
            spawner=magi_spawner,
            base_url=base_url,
        )

    @router.get("/conversations")
    async def get_conversations(request: Request):
        caller = auth_handle(request)
        return {"conversations": service.list_conversations(caller)}

    @router.patch("/bots/{handle}/nickname")
    async def patch_bot_nickname(handle: str, body: UpdateNicknameBody, request: Request):
        if auth_handle(request) != "user":
            raise HTTPException(status_code=403, detail="operator only")
        agent = store.get_agent(handle)
        if agent is None or handle == "user":
            raise HTTPException(status_code=404, detail="MAGI not found")
        nickname = body.nickname.strip()
        if not nickname or len(nickname) > 80 or any(char in nickname for char in "\r\n"):
            raise HTTPException(status_code=400, detail="nickname must be one line and 1–80 characters")
        try:
            updated = await transport.update_nickname(handle, nickname)
        except ConnectionError:
            raise HTTPException(status_code=503, detail="MAGI is offline")
        except TimeoutError:
            raise HTTPException(status_code=504, detail="MAGI did not confirm nickname")
        if not updated:
            raise HTTPException(status_code=502, detail="MAGI could not update nickname")
        agent.nickname = nickname
        return {"handle": handle, "nickname": nickname}

    # ---- Provider delivery; the app owns the saved configuration ----------

    async def sync_provider(
        settings: dict[str, Any], handles: list[str] | None
    ) -> tuple[list[str], list[dict[str, str]]]:
        synced: list[str] = []
        failed: list[dict[str, str]] = []
        for handle in dict.fromkeys(handles if handles is not None else store.agents):
            if handle == "user":
                continue
            if store.get_agent(handle) is None:
                continue
            try:
                if await transport.update_provider(handle, **settings):
                    synced.append(handle)
            except ConnectionError:
                # The app retries when this MAGI comes online.
                continue
            except TimeoutError:
                failed.append({"handle": handle, "detail": "MAGI did not confirm"})
        return synced, failed

    @router.get("/settings/provider/legacy")
    async def get_legacy_provider_settings(request: Request):
        if auth_handle(request) != "user":
            raise HTTPException(status_code=403, detail="operator only")
        return storage.get_setting(PROVIDER_SETTING_KEY)

    @router.delete("/settings/provider/legacy")
    async def delete_legacy_provider_settings(request: Request):
        if auth_handle(request) != "user":
            raise HTTPException(status_code=403, detail="operator only")
        storage.delete_setting(PROVIDER_SETTING_KEY)
        return {"ok": True}

    @router.put("/settings/provider")
    async def put_provider_settings(body: ProviderSettingsBody, request: Request):
        if auth_handle(request) != "user":
            raise HTTPException(status_code=403, detail="operator only")
        settings = {
            field: (getattr(body, field) or "").strip() or None
            for field in ("provider", "model", "api_key")
        }
        synced, failed = await sync_provider(settings, body.handles)
        return {**settings, "synced": synced, "failed": failed}

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request):
        caller = auth_handle(request)
        try:
            return service.conversation_view(caller, conversation_id)
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")

    @router.patch("/conversations/{conversation_id}")
    async def patch_conversation(
        conversation_id: str, body: UpdateConversationBody, request: Request
    ):
        caller = auth_handle(request)
        if body.topic is None and body.description is None:
            raise HTTPException(status_code=400, detail="nothing to update")
        try:
            return await service.update_conversation(
                caller, conversation_id, body.topic, body.description
            )
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        except NotAllowed:
            raise HTTPException(status_code=404, detail="not found")
        except Conflict as e:
            raise HTTPException(status_code=409, detail=str(e))

    @router.get("/bots")
    async def get_bots(request: Request, conversation_id: str | None = None):
        caller = auth_handle(request)
        try:
            return {"bots": service.list_bots(caller, conversation_id)}
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")

    @router.post("/conversations/{conversation_id}/members")
    async def post_conversation_member(
        conversation_id: str, body: AddMemberBody, request: Request
    ):
        caller = auth_handle(request)
        try:
            return await service.add_conversation_member(
                caller, conversation_id, body.handle
            )
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        except NotAllowed:
            raise HTTPException(status_code=404, detail="not found")
        except Conflict as e:
            raise HTTPException(status_code=409, detail=str(e))

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
                    transport.receive_control(agent.handle, message)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        # Schedule cleanup as a background task so the close ACK isn't
        # blocked behind session.disconnected fan-out.
        asyncio.create_task(transport.disconnect(agent.handle, ws))

    return AspOperator(
        router=router,
        store=store,
        transport=transport,
        service=service,
        spawner=magi_spawner,
        base_url=base_url,
    )
