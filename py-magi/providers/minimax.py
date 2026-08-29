"""Minimax — Anthropic-API-compatible chat completions.

Two regions surface to the rest of the system as
provider ids:

  - ``"minimax-cn"``     → ``https://api.minimaxi.com/anthropic``
  - ``"minimax-global"`` → ``https://api.minimax.io/anthropic``

Both use the same official ``anthropic`` SDK with
``base_url`` swapped to the chosen region; the wire
format is unchanged so we don't roll our own HTTP
client. This is a thin subclass of
:class:`providers.anthropic.AnthropicProvider`
that fixes the per-region config.

A bare ``"minimax"`` is treated as a synonym for
``"minimax-cn"``; the factory handles that.

Default model: ``MiniMax-M3``. Operators can override
per-MAGI by publishing a vNext ``ChangeProviderJob`` with ``model``.

Note on the URL path: the ``/anthropic`` segment is
part of the host's URL path, not a hint about the
wire format — Anthropic's own API is at
``https://api.anthropic.com`` (no extra path segment).
The SDK appends ``/v1/messages`` itself.
"""

from __future__ import annotations

from providers.anthropic import AnthropicProvider

# Base URLs as published by Minimax. Both routes are
# Anthropic-Messages-API-compatible. Hardcoded for v0
# — moving to env / settings once the user has a
# reason to point at a private deployment.
_BASE_URLS: dict[str, str] = {
    "minimax-cn": "https://api.minimaxi.com/anthropic",
    "minimax-global": "https://api.minimax.io/anthropic",
}

# Default model. Operators can override per-MAGI by
# publishing a vNext ``ChangeProviderJob``.
_DEFAULT_MODEL = "MiniMax-M3"


class MinimaxProvider(AnthropicProvider):
    """Minimax chat provider — single class, two regions.

    The factory instantiates one of two flavours by
    passing ``base_url=`` to the constructor (see
    :meth:`AnthropicProvider.__init__`). We don't
    subclass further (one per region) because the
    per-region diff is just the URL — anything else
    worth per-region override can land in
    :meth:`for_region` later.
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
    ) -> MinimaxProvider:
        """Return a Minimax provider for a specific region.

        ``region`` is either ``"minimax-cn"`` or
        ``"minimax-global"``. ``minimax`` is accepted
        as a synonym for ``minimax-cn`` (handled by
        the factory before calling here).

        The base class accepts an explicit
        ``base_url=`` kwarg, so we route through it
        instead of manufacturing a fresh subclass per
        call (which is what the previous
        implementation did and which made every
        ``get_provider`` invocation allocate a new
        class object — a tiny but real waste).
        """
        if region not in _BASE_URLS:
            from providers.errors import LLMError

            raise LLMError(f"Unknown minimax region: {region!r}. Known: {list(_BASE_URLS.keys())}")
        return cls(api_key=api_key, model=model, base_url=_BASE_URLS[region])
