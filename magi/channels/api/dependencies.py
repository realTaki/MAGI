"""Explicit FastAPI dependencies for app-scoped runtime objects."""

from __future__ import annotations

from fastapi import Request


def get_bus(request: Request):
    """Return the BUS explicitly attached to this ASGI application."""
    return request.app.state.bus


def get_workers(request: Request):
    return request.app.state.workers


__all__ = ["get_bus", "get_workers"]
