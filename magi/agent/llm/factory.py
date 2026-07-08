"""Provider factory — turn a string from ``Employee.provider``
into a ready-to-call ``LLMProvider``.

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

Adding a new provider:

1. Create ``magi/agent/llm/<name>.py`` subclassing
   :class:`AnthropicProvider` (or
   :class:`LLMProvider` for a non-Anthropic wire
   format).
2. Add a branch in :func:`get_provider` below.
3. Add the new id to :func:`known_providers` so the
   dashboard can populate the provider dropdown.
4. Add a row to :func:`provider_options_for_ui` so
   the operator sees a friendly label.

The factory is the single source of truth for "which
provider names are accepted". Validation runs in two
places: the API endpoint that accepts user input (so
the operator sees a 400 on a typo) and here (defensive
— the API might be bypassed by a direct DB write).
"""

from __future__ import annotations

import logging
from typing import Iterable

from magi.agent.llm.claude import ClaudeProvider
from magi.agent.llm.errors import LLMError
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
    ``get_provider`` still accepts ``"minimax"`` for
    backward compat with any pre-v0 employee rows;
    we just don't surface it in the picker.
    """
    return ["claude", "minimax-global", "minimax-cn"]


def get_provider(
    provider_name: str,
    api_key: str,
    model: str | None = None,
) -> LLMProvider:
    """Instantiate the right ``LLMProvider`` for ``provider_name``.

    Parameters
    ----------
    provider_name
        The id stored in ``Employee.provider`` (or the
        system default). Case-insensitive. ``"minimax"``
        alone is still accepted as a synonym for
        ``"minimax-cn"`` for backward compat — pre-v0
        employee rows that picked the bare alias keep
        working — but it no longer appears in the UI
        picker.
    api_key
        The vendor API key. Not logged.
    model
        Optional model override. ``None`` means "use the
        provider's default".
    """
    if not provider_name:
        raise LLMError("provider name is required")
    if not api_key:
        raise LLMError("api_key is required")

    name = provider_name.strip().lower()
    if name == "minimax" or name == "minimax-cn":
        return MinimaxProvider.for_region(
            "minimax-cn", api_key=api_key, model=model
        )
    if name == "minimax-global":
        return MinimaxProvider.for_region(
            "minimax-global", api_key=api_key, model=model
        )
    if name == "claude":
        return ClaudeProvider(api_key=api_key, model=model)

    raise LLMError(
        f"Unknown LLM provider: {provider_name!r}. "
        f"Known: {', '.join(known_providers())}"
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