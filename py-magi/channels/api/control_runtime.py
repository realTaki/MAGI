"""WebUI-to-root-runtime calls used before an operator can authenticate."""

from __future__ import annotations

import os

import httpx

from old_bus import Bus
from channels.api.proxy_auth import build_proxy_headers
from channels.api.runtime_http import RELAY_TIMEOUT


async def _post(bus: Bus, path: str, payload: dict[str, object]) -> None:
    headers = build_proxy_headers(
        bus=bus,
        method="POST",
        path_and_query=path,
        target_id=1,
        operator_id=0,
        operator_name="WebUI bootstrap",
        tgid=None,
        admin=False,
        assigned=False,
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


async def bootstrap_telegram(bus: Bus, token: str, username: str) -> None:
    await _post(bus, "/api/control/telegram/bootstrap", {"token": token, "username": username})


async def send_telegram(bus: Bus, tgid: int, text: str) -> None:
    await _post(bus, "/api/control/telegram/send", {"tgid": tgid, "text": text})
