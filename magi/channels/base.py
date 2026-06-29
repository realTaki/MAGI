"""Channel interface — implemented by every channel adapter.

Concrete channels (Telegram, WebUI, future email / calendar) live in
``channels/telegram/`` and ``channels/webui/``. They share this contract
so the runtime can treat them interchangeably.
"""

from __future__ import annotations

from typing import Protocol


class Channel(Protocol):
    """Minimum contract every channel adapter must satisfy.

    The runtime calls ``receive()`` to get the next inbound message,
    runs it through the agent loop, and calls ``send()`` to deliver the
    reply. ``identify_sender()`` resolves who the message is from (used
    by the audit log and the permission scope check).
    """

    name: str

    async def receive(self) -> "InboundMessage | None": ...

    async def send(self, target: "MessageTarget", content: "OutboundContent") -> None: ...

    def identify_sender(self, raw: object) -> "Sender": ...


class InboundMessage:
    """Placeholder — defined in C3 when the runtime lands."""


class OutboundContent:
    """Placeholder — defined in C3 when the runtime lands."""


class MessageTarget:
    """Placeholder — defined in C3 when the runtime lands."""


class Sender:
    """Placeholder — defined in C3 when the runtime lands."""