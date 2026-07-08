"""Minimax — Anthropic-API-compatible chat completions.

Two regions surface to the rest of the system as
provider ids:

  - ``"minimax-cn"``     → ``https://api.minimaxi.com/anthropic``
  - ``"minimax-global"`` → ``https://api.minimax.io/anthropic``

Both use the same official ``anthropic`` SDK with
``base_url`` swapped to the chosen region; the wire
format is unchanged so we don't roll our own HTTP
client. This is a thin subclass of
:class:`magi.agent.llm.anthropic.AnthropicProvider`
that fixes the per-region config.

A bare ``"minimax"`` in ``Employee.provider`` is
treated as a synonym for ``"minimax-cn"``; the
factory handles that.

Default model: ``MiniMax-M2.7``. Same override path
as Claude (``model=`` to ``get_provider``).

Note on the URL path: the ``/anthropic`` segment is
part of the host's URL path, not a hint about the
wire format — Anthropic's own API is at
``https://api.anthropic.com`` (no extra path segment).
The SDK appends ``/v1/messages`` itself.
"""

from __future__ import annotations

from magi.agent.llm.anthropic import AnthropicProvider


# Base URLs as published by Minimax. Both routes are
# Anthropic-Messages-API-compatible. Hardcoded for v0
# — moving to env / settings once the user has a
# reason to point at a private deployment.
_BASE_URLS: dict[str, str] = {
    "minimax-cn": "https://api.minimaxi.com/anthropic",
    "minimax-global": "https://api.minimax.io/anthropic",
}

# Default model. Operators can override per-employee
# by extending the Employee model with a model
# column (the ``employee_model`` argument passed to
# ``handle_message`` already accepts this).
_DEFAULT_MODEL = "MiniMax-M2.7"


class MinimaxProvider(AnthropicProvider):
    """Minimax chat provider — single class, two regions.

    The factory instantiates one of two flavours by
    setting ``_BASE_URL`` on the class after
    construction. We support that by exposing
    :func:`for_region` which returns a properly
    configured subclass instance.
    """

    _BASE_URL = _BASE_URLS["minimax-cn"]
    _DEFAULT_MODEL = _DEFAULT_MODEL
    _ERROR_LABEL = "minimax"

    @classmethod
    def for_region(
        cls,
        region: str,
        api_key: str,
        model: str | None = None,
    ) -> "MinimaxProvider":
        """Return a Minimax provider for a specific region.

        ``region`` is either ``"minimax-cn"`` or
        ``"minimax-global"``. ``minimax`` is accepted
        as a synonym for ``minimax-cn`` (handled by
        the factory before calling here).

        We don't subclass further (one per region)
        because the per-region diff is just the URL —
        anything else worth per-region override can
        land in this method later.
        """
        if region not in _BASE_URLS:
            from magi.agent.llm.errors import LLMError
            raise LLMError(
                f"Unknown minimax region: {region!r}. "
                f"Known: {list(_BASE_URLS.keys())}"
            )
        # Build an instance via the regular __init__,
        # then patch the URL on the client. The base
        # class builds the SDK client from
        # ``self._BASE_URL`` in __init__, so we
        # override the class attribute on a fresh
        # subclass for the duration of the instance.
        class _RegionMinimax(MinimaxProvider):
            pass
        _RegionMinimax._BASE_URL = _BASE_URLS[region]
        return _RegionMinimax(api_key=api_key, model=model)