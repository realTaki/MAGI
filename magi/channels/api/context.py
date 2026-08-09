"""Request-scoped API context.

The ASGI app binds its explicitly constructed BUS for the duration of each
request.  This replaces process-global channel BUS selection and keeps
independent Runtime and WebUI applications isolated in one interpreter.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.bus import Bus


_bus: ContextVar["Bus | None"] = ContextVar("magi_api_bus", default=None)


def bind_bus(bus: "Bus") -> Token["Bus | None"]:
    return _bus.set(bus)


def reset_bus(token: Token["Bus | None"]) -> None:
    _bus.reset(token)


def get_bus() -> "Bus":
    bus = _bus.get()
    if bus is None:
        raise RuntimeError("BUS unavailable outside an API request")
    return bus


__all__ = ["bind_bus", "get_bus", "reset_bus"]
