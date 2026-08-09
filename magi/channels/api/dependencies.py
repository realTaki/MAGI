"""Explicit runtime dependencies for the channel HTTP API."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from magi.channels.api.errors import MagiHTTPException


def get_bus(request: Request):
    """Return the BUS instance owned by this concrete ASGI application.

    The composition root creates the BUS and attaches it to ``app.state``.
    This intentionally has no fallback to package or process global state.
    """
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        raise MagiHTTPException(
            status_code=503,
            code="runtime.bus_unavailable",
            detail="BUS was not supplied while creating this application",
        )
    return bus


BusDep = Annotated[object, Depends(get_bus)]
