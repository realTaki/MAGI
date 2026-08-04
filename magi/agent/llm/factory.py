"""Provider factory — turn the seeded adam ``Magi``'s
``provider``/``api_key``/``model`` into a ready-to-call
``LLMProvider``.

Three providers ship in v0, all on the Anthropic
Messages API:

  - :class:`magi.agent.llm.claude.ClaudeProvider` —
    Anthropic's first-party Claude API.
  - :class:`magi.agent.llm.minimax.MinimaxProvider` —
    Minimax's two regions (China + Global), via the
    Anthropic-compatible endpoints.

All three subclass
:class:`magi.agent.llm.anthropic.AnthropicProvider`,
which centralises the SDK call, error mapping, and
response walking. The factory's job is just to pick
the right class + per-vendor config.

The factory is the **single source of truth** for the
MAGI runtime's LLM credentials. Every chat turn, task
fire, and compaction pass reads the adam ``Magi`` row
through this factory — callers never thread provider
name + key + model through their own signatures. If the
MAGIC hasn't been configured yet, :func:`get_provider`
raises :class:`LLMNotConfiguredError` so the chat
handler can return the operator a clear "set them in
智能体管理" 503.

Adding a new provider:

1. Create ``magi/agent/llm/<name>.py`` subclassing
   :class:`AnthropicProvider` (or
   :class:`LLMProvider` for a non-Anthropic wire
   format).
2. Add a branch in :func:`get_provider` below.
3. Add the new id to :func:`known_providers` so the
   dashboard can populate the provider dropdown.
4. Add a row to :func:`provider_options_for_ui` so the
   operator sees a friendly label.

The factory is the single source of truth for "which
provider names are accepted". Validation runs in two
places: the API endpoint that accepts user input (so
the operator sees a 400 on a typo) and here (defensive
— the API might be bypassed by a direct DB write).
"""

from __future__ import annotations

import logging
import os

from magi.agent.llm.claude import ClaudeProvider
from magi.agent.llm.errors import LLMError, LLMNotConfiguredError
from magi.agent.llm.minimax import MinimaxProvider
from magi.agent.llm.provider import LLMProvider

logger = logging.getLogger("magi.agent.llm.factory")


def known_providers() -> list[str]:
    """Provider ids the UI can offer in dropdowns.

    v0 ships the Anthropic-API-compatible family:
    Claude (Anthropic's first-party API) and the two
    Minimax regions. Order matches the dropdown:
    Claude first for international deployers, then
    the two Minimax regions for Asia-Pacific
    deployers. ``"minimax"`` (bare alias) is
    intentionally NOT listed here — operators pick a
    region explicitly so there's no ambiguity.
    :func:`get_provider` still accepts ``"minimax"`` for
    backward compat with any pre-v0 MAGIC rows.
    """
    return ["claude", "minimax-global", "minimax-cn"]


def get_provider(model: str | None = None) -> LLMProvider:
    """Resolve the MAGI runtime's LLM provider from the
    direct MAGIS database and instantiate it.

    The factory opens its own short-lived ORM session,
    resolves the runtime MAGI by ``MAGI_RUNTIME_ID`` (or the direct MAGIS's
    ADAM for the initial node),
    and uses that row's ``provider`` / ``api_key`` /
    ``model`` columns to build the provider.

    Parameters
    ----------
    model
        Optional per-call override. ``None`` means "use
        the model stored on the MAGIC row" (the
        operator's pick at PATCH time).

    Raises
    ------
    LLMNotConfiguredError
        The runtime MAGI's ``provider`` / ``api_key`` is
        unset. The chat / TG handler maps this to a
        503 ``magi.llm_credentials_required``.
    LLMError
        The configured provider id is not in
        :func:`known_providers` (typo, stale value).
    """
    from magi.bus import get_bus

    config = get_bus().magic.provider_configuration()
    if config is None:
        logger.warning("get_provider: no runtime MAGI with provider+api_key configured")
        raise LLMNotConfiguredError(
            "MAGI runtime has no LLM provider / API key configured; set it in MAGI management"
        )
    provider_name = config.provider
    api_key = config.api_key
    effective_model = model if model is not None else config.model

    if not provider_name:
        raise LLMError("provider name is required")
    if not api_key:
        raise LLMError("api_key is required")

    name = provider_name.strip().lower()
    if name == "minimax" or name == "minimax-cn":
        return MinimaxProvider.for_region("minimax-cn", api_key=api_key, model=effective_model)
    if name == "minimax-global":
        return MinimaxProvider.for_region("minimax-global", api_key=api_key, model=effective_model)
    if name == "claude":
        return ClaudeProvider(api_key=api_key, model=effective_model)

    raise LLMError(
        f"Unknown LLM provider: {provider_name!r}. Known: {', '.join(known_providers())}"
    )


def provider_options_for_ui() -> list[dict[str, str]]:
    """The dropdown entries for the provider picker.
    Each row has ``value`` (the id we store) and
    ``label`` (what the operator sees). New providers
    just add a row here.

    v0 ships the Anthropic-API-compatible family
    (Claude + the two Minimax regions). The factory
    and the picker stay in sync via
    :func:`known_providers`.
    """
    return [
        {"value": "claude", "label": "Anthropic (Claude)"},
        {"value": "minimax-global", "label": "Minimax (Global)"},
        {"value": "minimax-cn", "label": "Minimax (China)"},
    ]


def is_known_provider(name: str) -> bool:
    return name.strip().lower() in {n.lower() for n in known_providers()}


__all__ = [
    "get_provider",
    "known_providers",
    "provider_options_for_ui",
    "is_known_provider",
]
