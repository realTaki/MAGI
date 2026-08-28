"""LLM provider layer — abstracts the upstream chat API.

The runtime speaks one interface (:class:`LLMProvider`) regardless of
which vendor actually serves the request. vNext ships four concrete
implementations:

- :class:`magi.providers.claude_code.ClaudeProvider` — Anthropic's
  first-party API.
- :class:`magi.providers.minimax.MinimaxProvider` — Minimax's two
  regions (China + Global).
- :class:`magi.providers.openai.OpenAIProvider` — OpenAI's official
  chat-completions endpoint.

The Claude + Minimax pair subclass
:class:`magi.providers.anthropic.AnthropicProvider`, which
centralises the SDK call, error mapping, and response walking.
OpenAI is on a different wire format and subclasses
:class:`LLMProvider` directly. The factory in
:mod:`magi.providers.factory` is the single source of truth for
which provider id maps to which class.

Public surface
==============

This package is a **pure implementation** — it is consumed only by
:class:`~magi.providers.worker.ProvidersWorker` and the internal
submodules themselves. External modules interact with providers
exclusively through the bus job boards.

Everything else lives in the appropriate submodule:

- :class:`LLMProvider` / :class:`LLMStreamEvent` →
  :mod:`magi.providers.base`
- :class:`AnthropicProvider` →
  :mod:`magi.providers.anthropic`
- error classes (``LLMError`` / ``LLMAuthError`` / ...) →
  :mod:`magi.providers.errors`

Intentionally NOT exported here (intentional decoupling — "each
package does its own thing"):

- **error classes** — providers' internal taxonomy for mapping
  SDK exceptions to ``CallLLMResult.error_code`` strings. External
  code reads ``error_code`` directly and never catches the
  exception classes.
- ``LLMProvider`` / ``LLMStreamEvent`` — concrete providers and
  the worker import them from the submodule directly; no value in
  re-exporting.
- ``ChatMessage`` / ``ChatResult`` — deleted; wire format is plain
  ``list[dict]``.
- ``known_providers`` / ``is_known_provider`` /
  ``provider_options_for_ui`` — all deleted. The supported-provider
  list now lives at the ``providers.options`` default setting, which the
  worker registers through ``BusForWorker.boost_default_settings`` and the
  WebUI reads through BUS queries without importing :mod:`magi.providers`.
- ``enqueue_llm_job`` — callers publish vNext ``CallLLMJob`` through
  their ``BusForWorker`` slice.
- token estimators — moved to :mod:`magi.agent.tokens` since they
  serve the agent layer's compaction concern, not LLM calling.
"""
