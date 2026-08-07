"""LLM provider layer — abstracts the upstream chat API.

The runtime speaks one interface (:class:`LLMProvider`) regardless of
which vendor actually serves the request. v0 ships four concrete
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
exclusively through the new_bus job boards.

Re-exported here:

- :func:`get_provider` — kept solely so tests can monkey-patch via
  ``magi.providers.get_provider = fake``. Internal callers (the
  worker) reach the factory directly via
  :mod:`magi.providers.factory`; the re-export is a back-compat
  seam, not a recommended import path.

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
- ``known_providers`` / ``is_known_provider`` — kept module-private
  inside :mod:`magi.providers.factory`; the worker publishes the
  list to ``bus.settings[providers.options]`` at startup for WebUI.
- ``provider_options_for_ui` — deleted; same reason.
- ``enqueue_llm_job`` — deleted; callers do
  ``bus.llm_job_board.publish(CallLLMJob(...))``.
- token estimators — moved to :mod:`magi.agent.tokens` since they
  serve the agent layer's compaction concern, not LLM calling.
"""

from magi.providers.factory import get_provider

__all__ = ["get_provider"]
