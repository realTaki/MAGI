"""LLM provider layer — abstracts the upstream chat API.

The runtime speaks one interface (``LLMProvider``) regardless of
which vendor actually serves the request. v0 ships a single
concrete implementation, ``MinimaxProvider`` (Anthropic-API-
compatible, China + Global endpoints); later checkpoints add
``AnthropicProvider``, ``OpenAIProvider``, ``OllamaProvider``,
each as a new file plus a one-line entry in
:func:`magi.agent.llm.factory.get_provider`.

Public surface re-exported here so callers don't need to know
which submodule a class lives in::

    from magi.agent.llm import (
        LLMProvider, ChatMessage, ChatResult,
        LLMError, LLMAuthError, LLMNetworkError,
        get_provider,
    )
"""

from magi.agent.llm.errors import (
    LLMError,
    LLMAuthError,
    LLMRateLimitError,
    LLMNetworkError,
    LLMContextLengthError,
)
from magi.agent.llm.provider import (
    LLMProvider,
    ChatMessage,
    ChatResult,
)
from magi.agent.llm.factory import get_provider, is_known_provider, known_providers

__all__ = [
    "LLMProvider",
    "ChatMessage",
    "ChatResult",
    "LLMError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMNetworkError",
    "LLMContextLengthError",
    "get_provider",
    "is_known_provider",
    "known_providers",
]
