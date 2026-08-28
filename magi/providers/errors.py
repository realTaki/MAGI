"""Typed LLM errors.

Upstream providers surface many shapes of failure; the runtime
only needs to react to a handful:

  - ``LLMAuthError``         : bad / revoked API key. Don't retry;
                               the operator needs to rotate.
  - ``LLMRateLimitError``    : 429. Retry with backoff; might
                               also signal a quota exhaustion that
                               we should surface to the admin.
  - ``LLMContextLengthError``: input (system + messages) exceeded
                               the model's window. Caller can
                               either trim history or bubble up.
  - ``LLMNetworkError``      : transient network / 5xx. Retry.
  - ``LLMError``             : catch-all for everything else
                               (invalid model name, malformed
                               response body, etc.).

Each subclass carries the upstream message so the audit row can
hold the actual reason (not just "LLMError"). The provider worker
maps each class to its matching :class:`LLMErrorCode` member
(see ``_map_exception_to_code`` in ``providers/worker.py``); the
original exception message stays on :attr:`CallLLMResult.error`
for diagnostics.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class. All LLM-layer failures derive from this so
    callers can ``except LLMError`` to handle any provider
    failure uniformly."""


class LLMNotConfiguredError(LLMError):
    """No LLM provider / API key is configured for the runtime.

    The provider worker reads credentials through vNext
    ``GetSettingJob`` (keys ``provider.name`` / ``provider.api_key``)
    and passes them to :func:`magi.providers.factory.get_provider`.
    When either is unset, it raises this. Distinct from :class:`LLMAuthError`
    (key rejected by the vendor) — this one means the operator
    hasn't configured the runtime yet (and the worker's
    ``CallLLMResult.error_code`` will read
    ``"llm.credentials_required"``).
    """


class LLMAuthError(LLMError):
    """Upstream rejected the API key. Non-retryable."""


class LLMRateLimitError(LLMError):
    """Upstream returned 429. Retryable with backoff; the runtime
    itself does not retry in v0 — the caller can decide."""


class LLMNetworkError(LLMError):
    """Connectivity / 5xx / timeout. Retryable."""


class LLMContextLengthError(LLMError):
    """The system + messages payload exceeds the model's context
    window. The caller may want to truncate history before
    surfacing the error to the user."""
