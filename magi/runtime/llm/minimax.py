"""Minimax provider — Anthropic-API-compatible chat completions.

Minimax exposes the Anthropic Messages API over two base URLs
(China + Global). We use the official ``anthropic`` SDK with
``base_url`` swapped to the chosen region; the wire format is
unchanged so we don't roll our own HTTP client.

Two provider ids surface to the rest of the system:

  - ``"minimax-cn"``    → ``https://api.minimaxi.com/anthropic``
  - ``"minimax-global"`` → ``https://api.minimax.io/anthropic``

A bare ``"minimax"`` in ``Employee.provider`` is treated as a
synonym for ``"minimax-cn"``; the factory handles that.

Thinking blocks: the model's response may include
``type=thinking`` content blocks (chain-of-thought). They are
**never** sent to the user. The agent loop reads
``ChatResult.text`` for the reply and stashes
``ChatResult.thinking`` in the audit row for debugging.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anthropic

from magi.runtime.llm.errors import (
    LLMContextLengthError,
    LLMError,
    LLMAuthError,
    LLMNetworkError,
    LLMRateLimitError,
)
from magi.runtime.llm.provider import (
    ChatMessage,
    ChatResult,
    LLMProvider,
)

logger = logging.getLogger("magi.runtime.llm.minimax")

# Base URLs as published by Minimax. Both routes are
# Anthropic-Messages-API-compatible; the ``/anthropic`` segment
# is part of the host's URL path, not a hint about the wire
# format. Hardcoded for v0 — moving to env / settings once
# the user has a reason to point at a private deployment.
_BASE_URLS: dict[str, str] = {
    "minimax-cn": "https://api.minimaxi.com/anthropic",
    "minimax-global": "https://api.minimax.io/anthropic",
}

# Default model. Operators can override per-employee by
# extending the Employee model with a model column (the
# ``employee_model`` argument passed to ``handle_message``
# already accepts this).
_DEFAULT_MODEL = "MiniMax-M2.7"

# Cap on a single reply. 1024 is enough for most chat turns
# and well under the 8K cap most Anthropic-compatible APIs
# advertise. The Chat / Channel layer can ask for more if
# a specific use case needs it.
_MAX_TOKENS_DEFAULT = 1024


def _is_context_length_error(message: str) -> bool:
    """Heuristic — the SDK puts the upstream error text into
    the exception message. Most providers phrase context-length
    overflow as "prompt is too long" or "context length
    exceeded". Keep this loose; false positives just fall
    through to the generic LLMError.
    """
    m = message.lower()
    return (
        "context length" in m
        or "prompt is too long" in m
        or "maximum context" in m
        or "context_length" in m
    )


class MinimaxProvider(LLMProvider):
    """Minimax chat provider.

    Parameters
    ----------
    name
        ``"minimax-cn"`` or ``"minimax-global"``. The factory
        accepts a bare ``"minimax"`` and routes it to China.
    api_key
        The Minimax API key. Treat as a secret; do not log it.
    model
        Override the default model. Falls back to
        ``_DEFAULT_MODEL`` if ``None``.
    """

    def __init__(self, name: str, api_key: str, model: str | None = None) -> None:
        if name not in _BASE_URLS:
            # Defensive — the factory should have already
            # validated this, but a typo at the call site
            # is easier to debug if it explodes here.
            raise LLMError(f"Unknown minimax variant: {name!r}")
        super().__init__(api_key, model)
        self.name = name
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=_BASE_URLS[name],
            # Keep timeouts short — the agent loop is the
            # one waiting on this call. If the upstream is
            # slow, the caller (TG bot) gets a clear timeout
            # instead of a hung event loop.
            timeout=30.0,
        )

    def default_model(self) -> str:
        return _DEFAULT_MODEL

    async def chat(
        self,
        system: str | None,
        messages: list[ChatMessage],
        max_tokens: int = _MAX_TOKENS_DEFAULT,
    ) -> ChatResult:
        # Translate the runtime's flat message list into the
        # SDK's expected shape. ``content`` is a string for v0;
        # when C4 wires up tools we'll switch to a list of
        # typed blocks here.
        sdk_messages: list[dict[str, Any]] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": sdk_messages,
        }
        if system:
            kwargs["system"] = system

        try:
            # The SDK's ``messages.create`` is sync. Wrap in
            # to_thread so the FastAPI event loop stays free
            # for other requests while Minimax thinks.
            response = await asyncio.to_thread(
                self._client.messages.create, **kwargs
            )
        except anthropic.AuthenticationError as e:
            raise LLMAuthError(f"minimax auth failed: {e}") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMAuthError(f"minimax permission denied: {e}") from e
        except anthropic.RateLimitError as e:
            raise LLMRateLimitError(f"minimax rate limited: {e}") from e
        except anthropic.APITimeoutError as e:
            raise LLMNetworkError(f"minimax timeout: {e}") from e
        except anthropic.APIConnectionError as e:
            raise LLMNetworkError(f"minimax connection error: {e}") from e
        except anthropic.BadRequestError as e:
            # 400 covers invalid model name, malformed body,
            # and context-length overflow. Inspect the message
            # to split out the context-length case so the
            # caller can react (trim history) rather than
            # treating it as a generic 400.
            if _is_context_length_error(str(e)):
                raise LLMContextLengthError(f"minimax context overflow: {e}") from e
            raise LLMError(f"minimax bad request: {e}") from e
        except anthropic.APIStatusError as e:
            # Other 4xx / 5xx — treat as transient network-ish.
            raise LLMNetworkError(f"minimax status {e.status_code}: {e}") from e

        # Walk content blocks. Minimax's response carries the
        # same shape as Anthropic's: an ordered list of blocks
        # with a ``type`` discriminator. v0 cares about
        # ``text`` (the reply) and ``thinking`` (chain-of-
        # thought, audit-only). Future tool_use blocks will
        # be returned in ``raw_blocks`` but ignored here.
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        raw_blocks: list[dict[str, Any]] = []
        for block in response.content:
            # Pydantic models in the SDK expose ``model_dump``
            # since 0.30. Fall back to ``__dict__`` if the
            # installed version is older (defensive — the lock
            # pins 0.113 but tests might run on a different env).
            if hasattr(block, "model_dump"):
                raw = block.model_dump()
            elif hasattr(block, "dict"):
                raw = block.dict()
            else:
                raw = {"type": getattr(block, "type", "unknown")}
            raw_blocks.append(raw)

            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "thinking":
                thinking_parts.append(getattr(block, "thinking", ""))
            # tool_use / others: capture in raw_blocks, ignore
            # for the reply. C4 will route tool_use through the
            # skill runner.

        usage_obj = getattr(response, "usage", None)
        if usage_obj is not None and hasattr(usage_obj, "model_dump"):
            usage = usage_obj.model_dump()
        elif usage_obj is not None and hasattr(usage_obj, "dict"):
            usage = usage_obj.dict()
        else:
            usage = None

        text = "\n".join(p for p in text_parts if p).strip()
        thinking = "\n".join(p for p in thinking_parts if p).strip() or None

        return ChatResult(
            text=text or "(empty reply)",
            thinking=thinking,
            model=getattr(response, "model", self.model),
            usage=usage,
            raw_blocks=raw_blocks,
        )
