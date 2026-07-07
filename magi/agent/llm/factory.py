"""Provider factory — turn a string from ``Employee.provider``
into a ready-to-call ``LLMProvider``.

Adding a new provider:

1. Write a new module under ``magi/agent/llm/`` (e.g.
   ``anthropic.py``) that subclasses :class:`LLMProvider`.
2. Add a branch in :func:`get_provider` below.
3. Add the new id to :func:`known_providers` so the dashboard
   can populate the provider dropdown.

The factory is the single source of truth for "which provider
names are accepted". Validation runs in two places: the API
endpoint that accepts user input (so the operator sees a 400
on a typo) and here (defensive — the API might be bypassed by
a direct DB write).
"""

from __future__ import annotations

import logging
from typing import Iterable

from magi.agent.llm.errors import LLMError
from magi.agent.llm.minimax import MinimaxProvider
from magi.agent.llm.provider import LLMProvider

logger = logging.getLogger("magi.agent.llm.factory")


def known_providers() -> list[str]:
    """Provider ids the UI can offer in dropdowns.

    v0 ships only the Minimax endpoints. Order matches the
    dropdown: Global first for international deployers, then
    China. ``"minimax"`` (bare alias) is intentionally NOT
    listed here — operators pick a region explicitly so there's
    no ambiguity. ``get_provider`` still accepts ``"minimax"``
    for backward compat with any pre-v0 employee rows; we just
    don't surface it in the picker.
    """
    return ["minimax-global", "minimax-cn"]


def get_provider(
    provider_name: str,
    api_key: str,
    model: str | None = None,
) -> LLMProvider:
    """Instantiate the right ``LLMProvider`` for ``provider_name``.

    Parameters
    ----------
    provider_name
        The id stored in ``Employee.provider`` (or the system
        default). Case-insensitive. ``"minimax"`` alone is
        still accepted as a synonym for ``"minimax-cn"`` for
        backward compat — pre-v0 employee rows that picked
        the bare alias keep working — but it no longer
        appears in the UI picker.
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
        return MinimaxProvider(name="minimax-cn", api_key=api_key, model=model)
    if name == "minimax-global":
        return MinimaxProvider(name="minimax-global", api_key=api_key, model=model)

    raise LLMError(
        f"Unknown LLM provider: {provider_name!r}. "
        f"Known: {', '.join(known_providers())}"
    )


def provider_options_for_ui() -> list[dict[str, str]]:
    """The dropdown entries for the provider picker. Each row
    has ``value`` (the id we store) and ``label`` (what the
    operator sees). New providers just add a row here.

    v0 ships only Minimax. When OpenAI / Anthropic / etc.
    land, add them here and to :func:`known_providers` so
    the picker and the validator stay in sync.
    """
    return [
        {"value": "minimax-global", "label": "Minimax (Global)"},
        {"value": "minimax-cn", "label": "Minimax (China)"},
    ]


def is_known_provider(name: str) -> bool:
    return name.strip().lower() in {n.lower() for n in known_providers()}
