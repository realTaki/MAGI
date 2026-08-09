"""Channel Worker health endpoint — ``GET /health/channels``."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health/channels")
async def health_channels() -> dict:
    """Return health snapshot for all registered Channel Workers."""
    from magi.channels import registered_channel_workers

    workers = registered_channel_workers()
    return {
        "channels": [w.health() for w in workers.values()],
    }
