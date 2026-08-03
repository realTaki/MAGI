"""Control-plane proxy for private selected-MAGI Runtime APIs."""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Request, Response

from magi.bus import bootstrap
from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.proxy_auth import build_proxy_headers

router = APIRouter(tags=["runtime-proxy"])


def _runtime_url(magic_id: int) -> str:
    """Resolve the upstream URL for the chosen MAGI's runtime.

    Returns either ``http://<deployment>:42069`` when the EVA runtime
    is observed running, the root runtime URL when the requested
    MAGIC is the root MAGIS's ADAM, or raises 409 if the runtime
    isn't running.
    """
    bus = bootstrap(os.environ.get("MAGI_STATE_DIR", ""))
    runtime = bus.magic.get_runtime(magic_id)
    if runtime and runtime.deployment_name and runtime.observed_state not in {"stopped", "deleted"}:
        return f"http://{runtime.deployment_name}:42069"
    root_url = bus.magis.root_runtime_url(magic_id)
    if root_url is not None:
        return root_url
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
) -> Response:
    """Forward one browser request to the chosen MAGI's internal API.

    This route intentionally has no user-controlled upstream URL.  The target
    Service is derived from the MAGI registry and every forwarded request is
    HMAC-bound to both the selected MAGI and the signed-in operator.
    """
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise MagiHTTPException(status_code=400, code="runtime.path_invalid", detail="Invalid runtime path")
    from magi.channels.api.auth import selected_session

    browser_session = selected_session(request.cookies.get("magi_session"))
    if browser_session is None:
        raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in")
    if int(browser_session["magic_id"]) != magic_id:
        raise MagiHTTPException(status_code=403, code="auth.target_mismatch", detail="The session is bound to another MAGI")
    runtime_path = f"/api/{path}"
    if request.url.query:
        runtime_path = f"{runtime_path}?{request.url.query}"
    try:
        signed_headers = build_proxy_headers(
            method=request.method,
            path_and_query=runtime_path,
            target_id=magic_id,
            operator_id=int(browser_session["telegram_id"]),
            operator_name=(browser_session.get("display_name") if isinstance(browser_session.get("display_name"), str) else None) or f"User {browser_session['telegram_id']}",
            telegram_id=int(browser_session["telegram_id"]),
            admin=bool(browser_session.get("admin")),
            assigned=bool(browser_session.get("assigned")),
        )
    except RuntimeError as exc:
        raise MagiHTTPException(status_code=503, code="runtime.proxy_unavailable", detail=str(exc)) from exc
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            upstream = await client.request(
                request.method,
                _runtime_url(magic_id) + runtime_path,
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


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_selected_runtime(
    path: str,
    request: Request,
) -> Response:
    """Compatibility path for runtime APIs called without ``/runtime/<id>``.

    Most React Query calls are rewritten client-side, but a few independent
    controls use ``fetch('/api/...')``.  Keeping this server-side fallback
    means they cannot accidentally reach WebUI-local state.  Auth and
    onboarding routers are mounted earlier and retain their explicit paths.
    """
    from magi.channels.api.auth import selected_session

    browser_session = selected_session(request.cookies.get("magi_session"))
    if browser_session is None:
        raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in")
    return await proxy_runtime(int(browser_session["magic_id"]), path, request)
