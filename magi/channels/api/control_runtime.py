"""WebUI-to-root-runtime calls used before an operator can authenticate."""

from __future__ import annotations

import os

import httpx

from magi.channels.api.proxy_auth import build_proxy_headers
from magi.channels.api.runtime_http import RELAY_TIMEOUT


async def _post(path: str, payload: dict[str, object]) -> None:
    headers = build_proxy_headers(
        method="POST",
        path_and_query=path,
        target_id=1,
        operator_id=0,
        operator_name="WebUI bootstrap",
        tgid=None,
    )
    base = os.environ.get("MAGI_ROOT_RUNTIME_URL", "http://magi:42069")
    # Both endpoints this helper reaches (``/control/telegram/bootstrap``,
    # ``/control/telegram/send``) hand off to api.telegram.org on the far
    # side, so the read budget has to clear Telegram's own — see
    # :data:`RELAY_TIMEOUT`.
    async with httpx.AsyncClient(timeout=RELAY_TIMEOUT) as client:
        response = await client.post(base + path, json=payload, headers=headers)
    if response.is_error:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = None
        if detail:
            raise RuntimeError(str(detail))
    response.raise_for_status()


async def bootstrap_telegram(token: str, username: str) -> None:
    await _post("/api/control/telegram/bootstrap", {"token": token, "username": username})


async def send_telegram(tgid: int, text: str) -> None:
    await _post("/api/control/telegram/send", {"tgid": tgid, "text": text})
