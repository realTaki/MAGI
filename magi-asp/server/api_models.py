"""Request bodies shared by the operator and session HTTP APIs."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field


def _require_non_empty_content(value: Any) -> Any:
    """Schema bar from `common.json#/$defs/Content`: empty payloads are
    rejected. The string form needs `minLength: 1`, the array form
    needs `minItems: 1`, and a `TextPart` with empty `text` is rejected
    by `TextPart.text`'s own `minLength: 1`.
    """
    if isinstance(value, str):
        if len(value) == 0:
            raise ValueError("content must not be empty")
    elif isinstance(value, list):
        if len(value) == 0:
            raise ValueError("content must contain at least one part")
        for part in value:
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
                and len(part["text"]) == 0
            ):
                raise ValueError("text part must not be empty")
    return value


Content = Annotated[Any, AfterValidator(_require_non_empty_content)]


class InitialMessage(BaseModel):
    content: Content
    metadata: dict | None = None


class CreateSessionBody(BaseModel):
    invite: list[str] = Field(default_factory=list)
    topic: str | None = None
    initial_message: InitialMessage | None = None
    end_after_send: bool = False
    idempotency_key: str | None = None


class InviteBody(BaseModel):
    invite: list[str]


class SendMessageBody(BaseModel):
    content: Content
    idempotency_key: str | None = None
    metadata: dict | None = None


class AcknowledgeEventsBody(BaseModel):
    event_ids: list[str] = Field(max_length=500)


class ReopenBody(BaseModel):
    invite: list[str] | None = None
    initial_message: InitialMessage | None = None


class CreateConversationBody(BaseModel):
    kind: Literal["bot", "group"]


class UpdateConversationBody(BaseModel):
    topic: str | None = None
    description: str | None = None


class AddMemberBody(BaseModel):
    handle: str


class UpdateNicknameBody(BaseModel):
    nickname: str


class ProviderSettingsBody(BaseModel):
    """A complete, transient provider update from the app."""

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    handles: list[str] | None = None

