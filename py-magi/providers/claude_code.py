"""Anthropic Claude — first-party Anthropic Messages API.

Subclass of :class:`providers.anthropic.AnthropicProvider`
that points at Anthropic's own API. The Anthropic SDK
defaults to ``https://api.anthropic.com``; we set the
URL explicitly so the subclass is self-describing and
the ``_BASE_URL`` invariant in the base class is
checked.

Provider id: ``"claude"``. The worker's ``providers.options`` catalog
pairs this id with the default model below.

Default model: ``claude-opus-5``. The worker catalog also
offers ``claude-fable-5``.
Operators can override per-MAGI by writing
the provider configuration (the worker applies it on the next
``ChangeProviderJob``).
"""

from __future__ import annotations

from providers.anthropic import AnthropicProvider


class ClaudeProvider(AnthropicProvider):
    """Anthropic Claude — first-party API.

    Wire-compatible with every other Anthropic-API
    vendor; the only difference is the base URL and
    the default model. Inherits the SDK call, error
    mapping, and response walking from
    :class:`AnthropicProvider`.
    """

    # Anthropic's own API root. The SDK would pick
    # this default, but we set it explicitly so the
    # subclass self-describes.
    _BASE_URL = "https://api.anthropic.com"
    _DEFAULT_MODEL = "claude-opus-5"
    _ERROR_LABEL = "claude"
