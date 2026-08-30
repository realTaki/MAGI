"""Basic liveness for one MAGI runtime."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Confirm that this MAGI HTTP process is online."""
    return {"status": "ok"}
