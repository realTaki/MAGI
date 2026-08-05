"""Per-MAGI runtime self-provider endpoint.

The 2026-08 creation-flow refactor moved provider / API key / model
out of the shared ``magic`` row and into each MAGI's local
``runtime_settings.toml`` file.  This module exposes the edit surface:

    GET    /api/magic/self/provider   — read current settings
    PATCH  /api/magic/self/provider   — partial update
    DELETE /api/magic/self/provider   — clear (sets all three to None)

The endpoint always operates on the runtime that received the
request — admin edits to another MAGI's settings flow through the
WebUI proxy (``/api/runtime/{magic_id}/magic/self/provider``) and
land on the target MAGI's runtime, which writes its own local file.

Authorization is admin-only.  ``admin_gate`` accepts both the
browser cookie and the WebUI proxy's HMAC-signed operator, so a
direct curl with admin cookies works the same as a proxied request.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from magi.bus.runtime_settings import (
    RuntimeSettings,
    load_runtime_settings,
    save_runtime_settings,
)
from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.auth_gates import admin_gate
from magi.providers.factory import is_known_provider, known_providers

logger = logging.getLogger("magi.api.runtime_provider")

router = APIRouter(tags=["runtime-provider"])


class ProviderOut(BaseModel):
    """Read shape returned by ``GET /api/magic/self/provider``."""

    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    model: str | None = None


class ProviderPatch(BaseModel):
    """Body for ``PATCH /api/magic/self/provider``.

    All fields optional — a partial update only touches the
    fields the caller set.  ``api_key=""`` clears the stored
    credential.  ``provider=""`` is treated as a clear too.
    """

    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=128)


def _to_out(rs: RuntimeSettings) -> ProviderOut:
    return ProviderOut(
        provider=rs.provider,
        api_key_set=bool(rs.api_key),
        api_key_last4=(rs.api_key[-4:] if rs.api_key else None),
        model=rs.model,
    )


def _validate_provider(provider: str | None) -> str | None:
    """Coerce + validate the provider id; raise on unknown.

    Empty string is treated as "clear", not as "unknown".  ``None``
    means "field omitted by caller" and is returned verbatim so the
    patch leaves the existing value alone.
    """
    if provider is None:
        return None
    if provider == "":
        return None
    if not is_known_provider(provider):
        raise MagiHTTPException(
            status_code=400,
            code="validation.unknown_provider",
            detail=(
                f"unknown provider {provider!r}; "
                f"known: {', '.join(known_providers())}"
            ),
        )
    return provider.strip().lower()


def _admin_gate(request: Request) -> str:
    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


@router.get("/magic/self/provider", response_model=ProviderOut)
def get_self_provider(_admin: AdminGate) -> ProviderOut:
    rs = load_runtime_settings()
    return _to_out(rs)


@router.patch("/magic/self/provider", response_model=ProviderOut)
def patch_self_provider(payload: ProviderPatch, _admin: AdminGate) -> ProviderOut:
    current = load_runtime_settings()
    new_provider = _validate_provider(payload.provider)
    # Patch semantics: None means "leave alone", "" means "clear".
    # Pydantic ``model_fields_set`` distinguishes "omitted" from "None"
    # but the simpler rule below — any non-None entry in the payload
    # overwrites, None leaves alone — matches the operator's intent
    # for the WebUI save-all-three-fields flow.
    merged = RuntimeSettings(
        provider=new_provider if "provider" in payload.model_fields_set else current.provider,
        api_key=payload.api_key if "api_key" in payload.model_fields_set else current.api_key,
        model=payload.model if "model" in payload.model_fields_set else current.model,
    )
    save_runtime_settings(merged)
    return _to_out(merged)


@router.delete("/magic/self/provider", response_model=ProviderOut)
def delete_self_provider(_admin: AdminGate) -> ProviderOut:
    """Clear all three fields.  Useful for re-provisioning."""
    save_runtime_settings(RuntimeSettings())
    return _to_out(RuntimeSettings())


__all__ = ["router"]
