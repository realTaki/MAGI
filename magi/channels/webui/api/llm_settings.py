"""System-wide LLM defaults.

Employee-level provider + API key live on the ``Employee`` row
(see :mod:`magi.channels.webui.api.departments`). When an
employee hasn't been configured individually, the agent loop
falls back to these system-wide defaults — the deployer's
"house LLM" that every unbound EVE uses.

The keys in the ``meta`` table:

  - ``llm.default_provider``  → e.g. ``"minimax-cn"``
  - ``llm.default_api_key``   → the API key (write-only from
                                the API; GET returns a boolean
                                "is_set" flag instead of the
                                value, the same pattern the
                                employee endpoint uses for
                                LLM provider keys).
  - ``llm.default_model``     → optional model override; falls
                                back to the provider's default.

The :func:`read_default` helper below is the single read path
the agent loop uses, so the lookup is consistent everywhere
(TG channel, WebUI chat, future scheduled jobs).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from magi.channels.webui.api.departments import AdminGate
from magi.runtime.llm import is_known_provider, known_providers
from magi.runtime.state.settings import state_delete, state_get, state_set

logger = logging.getLogger("magi.api.llm_settings")

router = APIRouter(tags=["llm"])


def _state_dir() -> str:
    """The same env-var lookup every other admin endpoint uses.

    Duplicated rather than imported from
    :mod:`magi.channels.webui.api.auth` so this module has no
    cross-router coupling — keeping each API file independent
    means we can move things later without a re-plumbing pass.
    """
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")


class LLMSettingsOut(BaseModel):
    """The public view of the system LLM default.

    ``api_key_set`` is the boolean "is configured" flag — the
    actual key is write-only on PUT and never returned on GET,
    so a careless ``GET`` from a logging dashboard doesn't leak
    the secret.
    """

    provider: str | None = None
    model: str | None = None
    api_key_set: bool = False


class LLMSettingsUpdate(BaseModel):
    """Payload for ``PUT /api/llm/default``.

    All fields are optional; absent fields mean "don't change".
    To clear the configured key, send ``api_key=""`` (empty
    string). ``api_key=null`` would also work via the
    ``model_fields_set`` pattern but we use empty-string for
    symmetry with the employee endpoint.
    """

    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    # ``api_key`` is write-only. ``""`` clears the stored key;
    # absent means "don't change". This mirrors the
    # EmployeeUpdate semantics.
    api_key: Optional[str] = Field(default=None, max_length=512)


def read_default(state_dir: str | None = None) -> LLMSettingsOut:
    """The single read path for system LLM defaults.

    Returns a fully-populated ``LLMSettingsOut``. ``None`` for
    the fields means "not configured" (rather than an error)
    so callers can render the UI's empty state without a
    special-case.
    """
    sd = state_dir or _state_dir()
    provider = state_get(sd, "llm.default_provider")
    model = state_get(sd, "llm.default_model")
    api_key = state_get(sd, "llm.default_api_key")
    return LLMSettingsOut(
        provider=provider,
        model=model,
        api_key_set=bool(api_key),
    )


@router.get("/llm/default", response_model=LLMSettingsOut)
def get_llm_default(_admin: AdminGate) -> LLMSettingsOut:
    """Return the current system LLM default.

    No secrets in the response — see ``LLMSettingsOut`` for the
    boolean-only contract.
    """
    return read_default()


@router.put("/llm/default", response_model=LLMSettingsOut)
def put_llm_default(
    payload: LLMSettingsUpdate,
    _admin: AdminGate,
) -> LLMSettingsOut:
    """Update the system LLM default.

    Provider is validated against :func:`is_known_provider` so
    a typo doesn't silently create a broken config. The
    validation runs on send so the operator sees a 400, not a
    later "Unknown LLM provider" 500 from the agent loop.

    To clear the configuration, send ``{"api_key": ""}`` (or
    ``DELETE /api/llm/default``). Sending only a provider
    without an api_key is allowed — useful for "I want to use
    Minimax but haven't put the key in yet"; the agent loop
    will then 5xx at first call with a clear "no key"
    message.
    """
    sd = _state_dir()

    if "provider" in payload.model_fields_set and payload.provider is not None:
        if not is_known_provider(payload.provider):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown provider {payload.provider!r}. "
                    f"Known: {', '.join(known_providers())}"
                ),
            )
        state_set(sd, "llm.default_provider", payload.provider)

    if "model" in payload.model_fields_set:
        # model is optional; None clears the override so the
        # provider's default takes over.
        state_set(sd, "llm.default_model", payload.model or "")

    if "api_key" in payload.model_fields_set:
        # Empty string = clear. None = leave alone (handled by
        # the field default). The actual value is whatever the
        # operator typed — write it through.
        state_set(sd, "llm.default_api_key", payload.api_key or "")

    logger.info(
        "llm.default updated",
        extra={
            "provider": payload.provider,
            "model_set": "model" in payload.model_fields_set,
            "api_key_set": "api_key" in payload.model_fields_set
            and bool(payload.api_key),
        },
    )
    return read_default(sd)


@router.delete("/llm/default", status_code=204)
def delete_llm_default(_admin: AdminGate) -> Response:
    """Clear all three LLM default keys.

    Use this when rotating providers or decommissioning the
    system LLM. After the call, the agent loop will surface a
    clear "no LLM configured" error to the next inbound
    message instead of silently using a stale config.
    """
    sd = _state_dir()
    for key in (
        "llm.default_provider",
        "llm.default_api_key",
        "llm.default_model",
    ):
        state_delete(sd, key)
    return Response(status_code=204)


@router.get("/llm/providers", response_model=list[str])
def get_llm_providers(_admin: AdminGate) -> list[str]:
    """List the provider ids the factory knows about.

    The frontend uses this to populate the provider dropdown
    in the employee detail panel and (later) the Settings tab.
    """
    return known_providers()
