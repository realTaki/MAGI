"""Conversation writes for one MAGI runtime.

The local MAGI App owns its own configuration and cache. This router only
creates a runtime conversation and publishes durable ``ChatNotify`` work for
the Agent worker; it has no WebUI session, contact, or control-plane logic.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from bus import ChatNotify, CreateConversationJob, JobStatus
from channels.api.dependencies import BusDep
from channels.api.errors import MagiHTTPException

router = APIRouter(tags=["chat"])

_MAX_MESSAGE_CHARS = 8_000


class CreateConversationResponse(BaseModel):
    conversation_id: int


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_MESSAGE_CHARS)


class SendMessageResponse(BaseModel):
    job_id: int
    status: str = "accepted"


def _board_or_unavailable(bus, job_type):
    board = bus.board(job_type)
    if board is None:
        raise MagiHTTPException(
            status_code=503,
            code="runtime.job_board_unavailable",
            detail=f"{job_type.__name__} is unavailable on this MAGI",
        )
    return board


@router.post("/conversations", response_model=CreateConversationResponse, status_code=201)
def create_conversation(bus: BusDep) -> CreateConversationResponse:
    """Create an App-originated conversation through the BUS."""
    board = _board_or_unavailable(bus, CreateConversationJob)
    job_id = board.publish(
        CreateConversationJob(
            publisher="api",
            channel="app",
            delivery_address="app",
        )
    )
    result = board.get_result(job_id)
    if result is None or result.status is not JobStatus.COMPLETED or result.conversation_id is None:
        raise MagiHTTPException(
            status_code=500,
            code="conversation.create_failed",
            detail="MAGI could not create the conversation",
        )
    return CreateConversationResponse(conversation_id=result.conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SendMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def send_message(
    conversation_id: int,
    payload: SendMessageRequest,
    bus: BusDep,
) -> SendMessageResponse:
    """Publish one inbound turn for the Agent worker to claim."""
    text = payload.text.strip()
    if not text:
        raise MagiHTTPException(
            status_code=400,
            code="validation.text_required",
            detail="text must not be empty",
        )
    board = _board_or_unavailable(bus, ChatNotify)
    job_id = board.publish(
        ChatNotify(
            publisher="api",
            conversation_id=conversation_id,
            text=text,
        )
    )
    return SendMessageResponse(job_id=job_id)
