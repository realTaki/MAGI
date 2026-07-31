"""Control-plane proxy for private selected-MAGI Runtime APIs."""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from magi.agent.db import ControlOperator, EveRuntime, MAGIC, MAGIS
from magi.agent.db.magis import get_magis_session
from magi.channels.webui.api.auth_gates import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException
from magi.channels.webui.proxy_auth import build_proxy_headers
from magi.channels.webui import control_store

router = APIRouter(tags=["runtime-proxy"])


def _runtime_url(session: Session, magic_id: int) -> str:
    magic = session.get(MAGIC, magic_id)
    if magic is None:
        raise MagiHTTPException(status_code=404, code="magic.not_found", detail="MAGI not found")
    runtime = session.scalar(select(EveRuntime).where(EveRuntime.magic_id == magic_id))
    if runtime and runtime.deployment_name and runtime.observed_state not in {"stopped", "deleted"}:
        return f"http://{runtime.deployment_name}:42069"
    root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
    if root and root.adam_id == magic_id:
        return os.environ.get("MAGI_ROOT_RUNTIME_URL", "http://magi:42069")
    raise MagiHTTPException(
        status_code=409,
        code="runtime.not_running",
        detail="This MAGI is not running. Start it before opening private runtime data.",
    )


@router.api_route("/runtime/{magic_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_runtime(
    magic_id: int,
    path: str,
    request: Request,
    admin_uid: AdminGate,
    magis_session: Session = Depends(get_magis_session),
) -> Response:
    """Forward one browser request to the chosen MAGI's internal API.

    This route intentionally has no user-controlled upstream URL.  The target
    Service is derived from the MAGI registry and every forwarded request is
    HMAC-bound to both the selected MAGI and the signed-in operator.
    """
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise MagiHTTPException(status_code=400, code="runtime.path_invalid", detail="Invalid runtime path")
    try:
        control_uid = int(admin_uid)
    except ValueError as exc:
        raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in") from exc
    if control_store.enabled():
        operator = magis_session.get(ControlOperator, control_uid)
    else:
        from magi.agent.db import Contact, open_session
        with open_session() as control_session:
            operator = control_session.get(Contact, control_uid)
    if operator is None:
        raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in")
    runtime_path = f"/api/{path}"
    if request.url.query:
        runtime_path = f"{runtime_path}?{request.url.query}"
    try:
        signed_headers = build_proxy_headers(
            method=request.method,
            path_and_query=runtime_path,
            target_id=magic_id,
            operator_id=operator.id,
            operator_name=operator.display_name or getattr(operator, "name", None) or f"Admin {operator.id}",
            telegram_id=operator.telegram_id,
        )
    except RuntimeError as exc:
        raise MagiHTTPException(status_code=503, code="runtime.proxy_unavailable", detail=str(exc)) from exc
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            upstream = await client.request(
                request.method,
                _runtime_url(magis_session, magic_id) + runtime_path,
                content=body or None,
                headers={"content-type": request.headers.get("content-type", "application/json"), **signed_headers},
            )
    except httpx.HTTPError as exc:
        raise MagiHTTPException(status_code=503, code="runtime.unreachable", detail="Selected MAGI runtime is unreachable") from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
