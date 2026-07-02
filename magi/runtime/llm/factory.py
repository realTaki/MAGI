"""Provider factory — turn a string from ``Employee.provider``
into a ready-to-call ``LLMProvider``.

Adding a new provider:

1. Write a new module under ``magi/runtime/llm/`` (e.g.
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

from magi.runtime.llm.errors import LLMError
from magi.runtime.llm.minimax import MinimaxProvider
from magi.runtime.llm.provider import LLMProvider

logger = logging.getLogger("magi.runtime.llm.factory")


def known_providers() -> list[str]:
    """Provider ids the UI can offer in dropdowns.

    Order is the recommended display order: Global first for
    international deployers, then China, then the bare alias.
    """
    return ["minimax-global", "minimax-cn", "minimax"]


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
        default). Case-insensitive. ``"minimax"`` alone is a
        synonym for ``"minimax-cn"``.
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
    """
    return [
        {"value": "minimax-global", "label": "Minimax (Global)"},
        {"value": "minimax-cn", "label": "Minimax (China)"},
        {"value": "minimax", "label": "Minimax (default → China)"},
    ]


def is_known_provider(name: str) -> bool:
    return name.strip().lower() in {n.lower() for n in known_providers()}
