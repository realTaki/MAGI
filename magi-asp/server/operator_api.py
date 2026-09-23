"""Desktop operator HTTP API: conversations, MAGI roster, and settings delivery."""

from __future__ import annotations

from typing import Any, Callable

from db.database import LocalDatabase
from fastapi import APIRouter, HTTPException, Request

from .api_models import (
    AddMemberBody,
    CreateConversationBody,
    ProviderSettingsBody,
    UpdateConversationBody,
    UpdateNicknameBody,
)
from .operator_service import OperatorService
from .service import Conflict, NotAllowed, NotFound
from .store import Store
from .transport import Transport

# Legacy key retained only until the app has copied and removed old settings.
PROVIDER_SETTING_KEY = "provider"


def operator_router(
    operator: OperatorService,
    store: Store,
    transport: Transport,
    storage: LocalDatabase,
    auth_handle: Callable[[Request], str],
) -> APIRouter:
    router = APIRouter()

    def require_operator(request: Request) -> str:
        handle = auth_handle(request)
        if handle != "user":
            raise HTTPException(status_code=403, detail="operator only")
        return handle

    # ---- Conversations (operator plus-button) --------------------------

    @router.post("/conversations", status_code=201)
    async def post_conversations(body: CreateConversationBody, request: Request):
        creator = require_operator(request)
        return await operator.create_conversation(creator=creator, kind=body.kind)

    @router.get("/conversations")
    async def get_conversations(request: Request):
        caller = require_operator(request)
        return {"conversations": operator.list_conversations(caller)}

    @router.patch("/bots/{handle}/nickname")
    async def patch_bot_nickname(handle: str, body: UpdateNicknameBody, request: Request):
        require_operator(request)
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
        store.update_agent(agent)
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
                else:
                    failed.append({"handle": handle, "detail": "MAGI rejected provider configuration"})
            except ConnectionError:
                # The app retries when this MAGI comes online.
                continue
            except TimeoutError:
                failed.append({"handle": handle, "detail": "MAGI did not confirm"})
        return synced, failed

    @router.get("/settings/provider/legacy")
    async def get_legacy_provider_settings(request: Request):
        require_operator(request)
        return storage.get_setting(PROVIDER_SETTING_KEY)

    @router.delete("/settings/provider/legacy")
    async def delete_legacy_provider_settings(request: Request):
        require_operator(request)
        storage.delete_setting(PROVIDER_SETTING_KEY)
        return {"ok": True}

    @router.put("/settings/provider")
    async def put_provider_settings(body: ProviderSettingsBody, request: Request):
        require_operator(request)
        settings = {
            field: (getattr(body, field) or "").strip() or None
            for field in ("provider", "model", "api_key")
        }
        synced, failed = await sync_provider(settings, body.handles)
        return {**settings, "synced": synced, "failed": failed}

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request):
        caller = require_operator(request)
        try:
            return operator.conversation_view(caller, conversation_id)
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")

    @router.patch("/conversations/{conversation_id}")
    async def patch_conversation(
        conversation_id: str, body: UpdateConversationBody, request: Request
    ):
        caller = require_operator(request)
        if body.topic is None and body.description is None:
            raise HTTPException(status_code=400, detail="nothing to update")
        try:
            return await operator.update_conversation(
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
        caller = require_operator(request)
        try:
            return {"bots": operator.list_bots(caller, conversation_id)}
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")

    @router.post("/conversations/{conversation_id}/members")
    async def post_conversation_member(
        conversation_id: str, body: AddMemberBody, request: Request
    ):
        caller = require_operator(request)
        try:
            return await operator.add_conversation_member(
                caller, conversation_id, body.handle
            )
        except NotFound:
            raise HTTPException(status_code=404, detail="not found")
        except NotAllowed:
            raise HTTPException(status_code=404, detail="not found")
        except Conflict as e:
            raise HTTPException(status_code=409, detail=str(e))

    return router
