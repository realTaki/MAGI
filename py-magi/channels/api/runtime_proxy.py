"""Control-plane proxy for private selected-MAGI Runtime APIs."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response

from channels.api.dependencies import get_bus
from channels.api.errors import MagiHTTPException
from channels.api.proxy_auth import build_proxy_headers
from channels.api.runtime_http import PROXY_TIMEOUT, runtime_is_live

router = APIRouter(tags=["runtime-proxy"])


def _runtime_url(bus, magi_id: int) -> str:
    """Resolve the upstream URL for the chosen MAGI's runtime.

    Looks up the runtime row in the MAGIS ``runtime_state_book`` and uses
    its ``base_url``. Falls back to ``bus.magis_book.root_runtime_url``
    for K8s service-DNS compatibility when the row is missing.
    """
    if bus.runtime_state_book is not None:
        runtime = bus.runtime_state_book.get_by_runtime_id(runtime_id=magi_id)
        if runtime is not None and runtime.base_url:
            return runtime.base_url
    if bus.magis_book is not None:
        root_url = bus.magis_book.root_runtime_url(magi_id=magi_id)
        if root_url is not None:
            return root_url
    raise MagiHTTPException(
        status_code=409,
        code="runtime.not_running",
        detail="This MAGI is not running. Start it before opening private runtime data.",
    )


@router.api_route(
    "/runtime/{magi_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
)
async def proxy_runtime(
    magi_id: int,
    path: str,
    request: Request,
) -> Response:
    """Forward one browser request to the chosen MAGI's internal API.

    This route intentionally has no user-controlled upstream URL.  The target
    Service is derived from the MAGI registry and every forwarded request is
    HMAC-bound to both the selected MAGI and the signed-in operator.

    Cross-MAGI access: a non-admin session may only target its own
    selected MAGI; an admin session may target any MAGI.  The admin
    path is what powers the ``PATCH /api/runtime/{magi_id}/magi/self/provider``
    edit flow — WebUI operators configure provider / API key on a
    MAGI other than the one they're signed in to, without first
    switching sessions.
    """
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise MagiHTTPException(
            status_code=400, code="runtime.path_invalid", detail="Invalid runtime path"
        )
    from channels.api.auth import selected_session

    bus = get_bus(request)
    browser_session = selected_session(bus, request.cookies.get("magi_session"))
    if browser_session is None:
        raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in")
    session_magi_id = int(browser_session["magi_id"])
    session_is_admin = bool(browser_session.get("admin"))
    if session_magi_id != magi_id and not session_is_admin:
        raise MagiHTTPException(
            status_code=403,
            code="auth.target_mismatch",
            detail="The session is bound to another MAGI",
        )
    runtime_path = f"/api/{path}"
    if request.url.query:
        runtime_path = f"{runtime_path}?{request.url.query}"
    raw_display_name = browser_session.get("display_name")
    named_display_name = raw_display_name if isinstance(raw_display_name, str) else None
    try:
        signed_headers = build_proxy_headers(
            bus=bus,
            method=request.method,
            path_and_query=runtime_path,
            target_id=magi_id,
            operator_id=int(
                browser_session.get("magis_admin_id") or browser_session["contact_id"]
            ),
            operator_name=named_display_name or f"User {browser_session['contact_id']}",
            tgid=(
                int(browser_session["tgid"])
                if browser_session.get("tgid") is not None
                else None
            ),
            magis_admin_id=(
                int(browser_session["magis_admin_id"])
                if browser_session.get("magis_admin_id") is not None
                else None
            ),
            admin=bool(browser_session.get("admin")),
            assigned=bool(browser_session.get("assigned")),
            two_factor=bool(browser_session.get("two_factor")),
        )
    except RuntimeError as exc:
        raise MagiHTTPException(
            status_code=503, code="runtime.proxy_unavailable", detail=str(exc)
        ) from exc
    body = await request.body()
    upstream_base = _runtime_url(get_bus(request), magi_id)
    # ``PROXY_TIMEOUT`` deliberately keeps a 60-second read budget for
    # the handful of endpoints that block on third parties (MCP tool
    # discovery), which makes it useless as an availability detector:
    # an unresponsive Runtime can accept the connection yet stall the
    # forwarded request for the full minute. Probe first — see
    # :func:`runtime_is_live`.
    if not await runtime_is_live(upstream_base):
        raise MagiHTTPException(
            status_code=503,
            code="runtime.unreachable",
            detail="Selected MAGI runtime is unavailable or unreachable",
        )
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
            upstream = await client.request(
                request.method,
                upstream_base + runtime_path,
                content=body or None,
                headers={
                    "content-type": request.headers.get("content-type", "application/json"),
                    **signed_headers,
                },
            )
    except httpx.HTTPError as exc:
        raise MagiHTTPException(
            status_code=503,
            code="runtime.unreachable",
            detail="Selected MAGI runtime is unreachable",
        ) from exc
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
    means they cannot accidentally reach WebUI-local state. Auth routes are
    mounted earlier and retain their explicit paths.
    """
    from channels.api.auth import selected_session

    browser_session = selected_session(get_bus(request), request.cookies.get("magi_session"))
    if browser_session is None:
        raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in")
    return await proxy_runtime(int(browser_session["magi_id"]), path, request)
