"""Wire contracts shared by Adam's client and the orchestrator service."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EveSpec(BaseModel):
    magic_id: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=100)
    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=128)
    personal_instruction: str = Field(default="", max_length=12000)
    memberships: list[dict[str, str]] = Field(default_factory=list, max_length=3)


class EveOperationResult(BaseModel):
    observed_state: str
    namespace: str
    deployment_name: str
    workspace_claim_name: str
    credential_secret_name: str
    message: str | None = None
