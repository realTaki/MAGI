"""Compose the ASP operator and participant protocol APIs."""

from __future__ import annotations

from dataclasses import dataclass

from db.database import LocalDatabase
from fastapi import APIRouter, HTTPException, Request

from .operator_api import operator_router
from .operator_service import OperatorService
from .service import SessionService
from .session_api import session_router
from .spawn import MagiSpawner, default_spawner
from .store import Store
from .transport import Transport


@dataclass
class AspRuntime:
    """The ASP subsystem owned by the ASP server process."""

    router: APIRouter
    store: Store
    transport: Transport
    sessions: SessionService
    spawner: MagiSpawner
    base_url: str = "http://127.0.0.1:42069"
    seed: dict[str, str | dict] | None = None

    async def close(self) -> None:
        self.spawner.close()
        await self.transport.close()


def create_asp_runtime(
    seed: dict[str, str],
    *,
    storage: LocalDatabase,
    spawner: MagiSpawner | None = None,
    base_url: str = "http://127.0.0.1:42069",
) -> AspRuntime:
    """Create one ASP runtime with two explicit API surfaces."""
    store = Store(storage)
    transport = Transport(store)
    service = SessionService(store, transport)
    magi_spawner = spawner if spawner is not None else default_spawner()
    operator = OperatorService(service, store, transport, magi_spawner, base_url)

    def auth_handle(request: Request) -> str:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing credentials")
        token = auth_header.removeprefix("Bearer ")
        agent = store.authenticate(token)
        if agent is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        return agent.handle

    router = APIRouter()
    router.include_router(
        operator_router(operator, store, transport, storage, auth_handle)
    )
    router.include_router(session_router(service, store, transport, auth_handle))
    return AspRuntime(
        router=router,
        store=store,
        transport=transport,
        sessions=service,
        spawner=magi_spawner,
        base_url=base_url,
        seed=seed,
    )
