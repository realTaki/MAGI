"""Read and stream actor-run state for asynchronous channels."""

from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from magi.bus import bootstrap, get_stream_hub
from magi.channels.api.auth_gates import AdminGate

router = APIRouter(tags=["runs"])


def _bus():
    return bootstrap(os.environ.get("MAGI_STATE_DIR", ""))


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    reply: str | None = None
    error_code: str | None = None
    error_detail: str | None = None


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run(run_id: str, _admin: AdminGate) -> RunStatusResponse:
    result = _bus().agent_runs.result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunStatusResponse(
        run_id=result.run_id,
        status=result.status,
        reply=result.reply,
        error_code=result.error_code,
        error_detail=result.error_detail,
    )


@router.post("/runs/{run_id}/cancel", response_model=RunStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(run_id: str, _admin: AdminGate) -> RunStatusResponse:
    """Explicit cancellation endpoint; ordinary chat input is always steering."""
    agent_runs = _bus().agent_runs
    if not agent_runs.cancel(run_id):
        result = agent_runs.result(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="run not found")
    result = agent_runs.result(run_id)
    assert result is not None
    return RunStatusResponse(
        run_id=result.run_id,
        status=result.status,
        reply=result.reply,
        error_code=result.error_code,
        error_detail=result.error_detail,
    )


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, _admin: AdminGate) -> StreamingResponse:
    """Best-effort SSE; clients recover missed data from ``GET /runs/{id}``."""
    async def event_stream():
        queue = get_stream_hub().subscribe(run_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    result = _bus().agent_runs.result(run_id)
                    if result is not None and result.status in {"completed", "failed", "cancelled"}:
                        return
                    continue
                payload = {
                    "run_id": event.run_id,
                    "attempt_id": event.attempt_id,
                    "sequence_number": event.sequence_number,
                    "kind": event.kind,
                    "payload": event.payload,
                }
                yield f"event: {event.kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if event.kind == "message.committed":
                    return
        finally:
            get_stream_hub().unsubscribe(run_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
