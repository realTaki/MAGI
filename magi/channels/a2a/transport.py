"""A2A outbox transport; all network I/O stays outside SQLite transactions."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from magi.channels.a2a.adapter import A2AAdapter
from magi.channels.a2a.protocol import sign_request


async def send_a2a_delivery(target_magic_id: int, event_id: str, payload: dict[str, Any]) -> None:
    runtime_id = os.environ.get("MAGI_RUNTIME_ID", "")
    if not runtime_id.isdigit():
        raise RuntimeError("MAGI_RUNTIME_ID is required for A2A delivery")
    destination = A2AAdapter().lookup_im_id(target_magic_id)
    if not destination:
        raise RuntimeError(f"no A2A route for magic {target_magic_id}")
    body = {
        "event_id": event_id,
        "from_magic_id": int(runtime_id),
        "text": str(payload.get("text") or ""),
        "ts": datetime.now(timezone.utc).isoformat(),
        "reply_to": payload.get("reply_to"),
        "correlation_id": payload.get("correlation_id"),
    }
    encoded = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    headers = {"content-type": "application/json", **sign_request(magic_id=int(runtime_id), body=encoded)}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(f"http://{destination}/a2a/inbox", content=encoded, headers=headers)
    if response.status_code != 202:
        raise RuntimeError(f"A2A peer rejected delivery: {response.status_code} {response.text[:300]}")
