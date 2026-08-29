"""Channel Worker health endpoint — ``GET /health/channels``."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health/channels")
async def health_channels(request: Request) -> dict:
    """Return the channel subset of the startup-owned worker registry."""
    registry = getattr(request.app.state, "workers", None)
    if registry is None:
        return {"channels": []}
    return {"channels": [w.health() for w in registry.channel_workers().values()]}


@router.get("/health/workers")
async def health_workers(request: Request) -> dict:
    """Return health snapshots for every Worker in this runtime."""
    registry = getattr(request.app.state, "workers", None)
    return {"workers": registry.health() if registry is not None else []}
