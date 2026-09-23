from __future__ import annotations

import asyncio
import json

import pytest

import channels.asp.client as asp_client
from channels.asp.client import AspClient


@pytest.mark.asyncio
async def test_listener_reconnects_and_sends_event_receipt(monkeypatch) -> None:
    attempts = 0
    sent: list[dict] = []
    received = asyncio.Event()

    class Socket:
        async def send(self, payload: str) -> None:
            sent.append(json.loads(payload))
            received.set()

        async def __aiter__(self):
            yield json.dumps({"type": "session.message", "event_id": "evt_1"})
            await asyncio.Event().wait()

    class Connection:
        async def __aenter__(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("ASP is restarting")
            return Socket()

        async def __aexit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(asp_client.websockets, "connect", lambda *_args, **_kwargs: Connection())
    client = AspClient(handle="@unit.magi", base="http://asp.test", token="token")
    ready = asyncio.Event()

    async def on_event(_event):
        return {"type": "session.ack", "event_id": "evt_1"}

    listener = asyncio.create_task(client.listen(on_event, ready=ready))
    try:
        await asyncio.wait_for(received.wait(), timeout=3)
        assert ready.is_set()
        assert attempts == 2
        assert sent == [{"type": "session.ack", "event_id": "evt_1"}]
    finally:
        listener.cancel()
        with pytest.raises(asyncio.CancelledError):
            await listener
