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
hold the actual reason (not just "LLMError").
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class. All LLM-layer failures derive from this so
    callers can ``except LLMError`` to handle any provider
    failure uniformly."""


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
