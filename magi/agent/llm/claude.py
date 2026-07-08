"""Anthropic Claude — first-party Anthropic Messages API.

Subclass of :class:`magi.agent.llm.anthropic.AnthropicProvider`
that points at Anthropic's own API. The Anthropic SDK
defaults to ``https://api.anthropic.com``; we set the
URL explicitly so the subclass is self-describing and
the ``_BASE_URL`` invariant in the base class is
checked.

Provider id: ``"claude"``. Operator-facing label
(``provider_options_for_ui``): ``"Anthropic (Claude)"``.

Default model: ``claude-opus-4-7`` — Anthropic's
strongest general-purpose model as of the v0 cut.
Operators can override per-employee by passing
``model=`` to ``get_provider(...)`` (or the
``employee_model`` argument on the agent loop).
"""

from __future__ import annotations

from magi.agent.llm.anthropic import AnthropicProvider


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
    _DEFAULT_MODEL = "claude-opus-4-7"
    _ERROR_LABEL = "claude"