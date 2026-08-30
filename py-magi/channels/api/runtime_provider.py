"""Per-MAGI runtime self-provider endpoint.

The 2026-08 creation-flow refactor moved provider / API key / model
into each MAGI's local ``settings_book``.  This module exposes

    GET    /api/magi/self/provider   — read current settings
    PATCH  /api/magi/self/provider   — partial update
    DELETE /api/magi/self/provider   — clear (sets all three to None)

The endpoint always operates on the runtime that received the
request -- admin edits to another MAGI's settings flow through the
WebUI proxy (``/api/runtime/{magi_id}/magi/self/provider``) and
land on the target MAGI's runtime, which writes its own local file.

Authorization is admin-only.  ``admin_gate`` accepts both the
browser cookie and the WebUI proxy's HMAC-signed operator, so a
direct curl with admin cookies works the same as a proxied request.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from old_bus.firmwares.jobs.changeProviderConfigJob import (
    PROVIDER_API_KEY_KEY,
    PROVIDER_MODEL_KEY,
    PROVIDER_NAME_KEY,
    ChangeProviderConfigJob,
)
from channels.api.auth_gates import admin_gate
from channels.api.dependencies import BusDep
from channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.runtime_provider")

router = APIRouter(tags=["runtime-provider"])

# Setting key under which ProvidersWorker publishes the supported
# provider+model catalog (JSON array of {"provider": ..., "model": ...}).
# Fallback used when the worker hasn't seeded settings yet.
_PROVIDERS_OPTIONS_KEY = "providers.options"
_FALLBACK_KNOWN_PROVIDERS = {
    "claude",
    "openai",
    "gemini",
    "xai",
    "deepseek",
    "mistral",
    "minimax-cn",
    "minimax-global",
}


# -- request / response schemas -----------------------------------------


class ProviderOut(BaseModel):
    """Read shape returned by ``GET /api/magi/self/provider``."""

    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    model: str | None = None


class ProviderPatch(BaseModel):
    """Body for ``PATCH /api/magi/self/provider``.

    All fields optional -- a partial update only touches the
    fields the caller set.  ``api_key=""`` clears the stored
    credential.  ``provider=""`` is treated as a clear too.
    """

    provider: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=128)


# -- helpers ------------------------------------------------------------


def _known_providers(bus) -> set[str]:
    """Return the set of known provider ids from settings_book.

    Falls back to MAGI's curated LiteLLM host ids when the
    worker hasn't seeded ``providers.options`` yet.
    """
    raw = bus.settings_book.get_value(key=_PROVIDERS_OPTIONS_KEY)
    if raw:
        try:
            options = json.loads(raw)
            return {o["provider"] for o in options}
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    return _FALLBACK_KNOWN_PROVIDERS


def _validate_provider(bus, provider: str | None) -> str | None:
    """Coerce + validate the provider id; raise on unknown.

    Empty string is treated as "clear", not as "unknown".  ``None``
    means "field omitted by caller" and is returned verbatim so the
    patch leaves the existing value alone.
    """
    if provider is None:
        return None
    if provider == "":
        return None
    if provider.strip().lower() not in _known_providers(bus):
        known_str = ", ".join(sorted(_known_providers(bus)))
        raise MagiHTTPException(
            status_code=400,
            code="validation.unknown_provider",
            detail=(f"unknown provider {provider!r}; known: {known_str}"),
        )
    return provider.strip().lower()


def _read_current(bus) -> tuple[str | None, str | None, str | None]:
    """Return ``(provider, api_key, model)`` from settings_book."""
    return (
        bus.settings_book.get_value(key=PROVIDER_NAME_KEY),
        bus.settings_book.get_value(key=PROVIDER_API_KEY_KEY),
        bus.settings_book.get_value(key=PROVIDER_MODEL_KEY),
    )


def _to_out(provider: str | None, api_key: str | None, model: str | None) -> ProviderOut:
    return ProviderOut(
        provider=provider,
        api_key_set=bool(api_key),
        api_key_last4=(api_key[-4:] if api_key else None),
        model=model,
    )


def _admin_gate(request: Request) -> str:
    return admin_gate(request)


AdminGate = Annotated[str, Depends(_admin_gate)]


# -- endpoints ----------------------------------------------------------


@router.get("/magi/self/provider", response_model=ProviderOut)
def get_self_provider(_admin: AdminGate, bus: BusDep) -> ProviderOut:
    provider, api_key, model = _read_current(bus)
    return _to_out(provider, api_key, model)


@router.patch("/magi/self/provider", response_model=ProviderOut)
def patch_self_provider(payload: ProviderPatch, _admin: AdminGate, bus: BusDep) -> ProviderOut:
    current_provider, current_api_key, current_model = _read_current(bus)
    new_provider = _validate_provider(bus, payload.provider)
    # Patch semantics: None means "leave alone", "" means "clear".
    # Pydantic ``model_fields_set`` distinguishes "omitted" from "None"
    # but the simpler rule below -- any non-None entry in the payload
    # overwrites, None leaves alone -- matches the operator's intent
    # for the WebUI save-all-three-fields flow.
    merged_provider = new_provider if "provider" in payload.model_fields_set else current_provider
    merged_api_key = payload.api_key if "api_key" in payload.model_fields_set else current_api_key
    merged_model = payload.model if "model" in payload.model_fields_set else current_model
    # Publish through the job board: self-contained write that
    # sets settings_book + enqueues a worker job to rebuild
    # the cached SDK client.
    bus.change_provider_config_job_board.publish(
        ChangeProviderConfigJob(
            provider=merged_provider,
            api_key=merged_api_key,
            model=merged_model,
        )
    )
    return _to_out(merged_provider, merged_api_key, merged_model)


@router.delete("/magi/self/provider", response_model=ProviderOut)
def delete_self_provider(_admin: AdminGate, bus: BusDep) -> ProviderOut:
    """Clear all three fields.  Useful for re-provisioning."""
    bus.change_provider_config_job_board.publish(
        ChangeProviderConfigJob(provider=None, api_key=None, model=None)
    )
    return ProviderOut()


__all__ = ["router"]
