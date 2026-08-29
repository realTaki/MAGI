"""Shared helpers for provider implementations.

Houses the two pieces of logic that were previously duplicated across
:meth:`anthropic.AnthropicProvider` and :class:`openai.OpenAIProvider`:

- :func:`is_context_length_error` — heuristic that maps an upstream
  error message to the typed :class:`~magi.providers.errors.LLMContextLengthError`.
  Both SDKs use different phrasings (``"context_length_exceeded"``,
  ``"reduce the length"``, etc.) so we keep one canonical marker list.
- :func:`safe_dump` — best-effort Pydantic → ``dict``. The two SDKs
  ship slightly different APIs (``model_dump`` everywhere today,
  ``dict`` on older Pydantic v1, ``to_dict`` on some OpenAI types);
  we try them in order and return ``None`` on total failure.
"""

from __future__ import annotations

from typing import Any

# Canonical substring markers used by upstream "context overflow" errors.
# Order matters only for documentation; matching is case-insensitive.
_CONTEXT_LENGTH_MARKERS: tuple[str, ...] = (
    "context length",
    "context_length",
    "maximum context",
    "prompt is too long",
    "reduce the length",
    "tokens must be reduced",
)


def is_context_length_error(message: str | BaseException) -> bool:
    """Return ``True`` if ``message`` looks like a context-window overflow.

    Provider SDKs stuff the upstream human-readable text into the
    exception message; both Anthropic and OpenAI use a handful of
    standard phrasings. A false negative falls back to
    :class:`~magi.providers.errors.LLMError`, which is the worst-case
    outcome and acceptable — the operator still sees the failure.
    """
    m = str(message).lower()
    return any(marker in m for marker in _CONTEXT_LENGTH_MARKERS)


def safe_dump(obj: Any) -> dict[str, Any] | None:
    """Best-effort Pydantic / SDK object → ``dict``.

    Tries (in order): ``model_dump`` (Pydantic v2 / current SDKs),
    ``dict`` (Pydantic v1), ``to_dict`` (some OpenAI types),
    ``__dict__`` (last-ditch). Returns ``None`` when nothing works so
    callers can decide how to react (most fall back to ``{}``).
    """
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return dict(obj.__dict__)
        except Exception:
            return None
    return None


__all__ = ["is_context_length_error", "safe_dump"]
