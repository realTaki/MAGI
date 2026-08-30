"""Small Webapp REST surface for the browser UI."""

from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
