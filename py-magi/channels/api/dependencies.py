"""Explicit FastAPI dependencies for app-scoped runtime objects."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from bus import Bus


def get_bus(request: Request) -> Bus:
    """Return the BUS explicitly attached to this ASGI application."""
    return request.app.state.bus


BusDep = Annotated[Bus, Depends(get_bus)]


__all__ = ["BusDep", "get_bus"]
