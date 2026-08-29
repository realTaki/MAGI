"""WebUI channel package — browser-based operator surface.

The WebUI channel worker handles **outbound** delivery from the
agent back to the browser session (via ``messages_book``). Inbound
ingress is handled by the FastAPI ``/chat/send`` route in
``magi/channels/api/chat.py``.
"""

from __future__ import annotations
