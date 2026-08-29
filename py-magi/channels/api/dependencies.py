"""Explicit FastAPI dependencies for app-scoped runtime objects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from old_bus import Bus

if TYPE_CHECKING:
    from startup.workers import WorkerRegistry


def get_bus(request: Request) -> Bus:
    """Return the BUS explicitly attached to this ASGI application."""
    return request.app.state.bus


def get_workers(request: Request) -> WorkerRegistry:
    return request.app.state.workers


BusDep = Annotated[Bus, Depends(get_bus)]
WorkersDep = Annotated["WorkerRegistry", Depends(get_workers)]


__all__ = ["BusDep", "WorkersDep", "get_bus", "get_workers"]
