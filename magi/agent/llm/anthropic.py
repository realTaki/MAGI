"""Shared base for Anthropic-API-compatible chat completions.

Both :class:`magi.agent.llm.claude.ClaudeProvider`
(Anthropic's first-party API) and
:class:`magi.agent.llm.minimax.MinimaxProvider`
(Minimax's Anthropic-compatible endpoints) subclass
this. The two vendors speak the same wire format
(Anthropic Messages API) — the only differences are
the base URL, the default model, and the error-label
string the operator sees in logs. The base class
centralises:

  - the SDK client construction (with timeout)
  - the ``messages.create`` call
  - the error mapping (auth / rate-limit / network /
    context-length / generic 4xx-5xx)
  - the response walking (text / thinking / tool_use
    extraction; everything else captured in
    ``raw_blocks``)

Subclasses just override three class attributes
(``_BASE_URL``, ``_DEFAULT_MODEL``, ``_ERROR_LABEL``)
and that's the whole "vendor" surface.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anthropic

from magi.agent.llm.errors import (
    LLMContextLengthError,
    LLMError,
    LLMAuthError,
    LLMNetworkError,
    LLMRateLimitError,
)
from magi.agent.llm.provider import (
    ChatMessage,
    ChatResult,
    LLMProvider,
)

logger = logging.getLogger("magi.agent.llm.anthropic")

# Cap on a single reply. 1024 is enough for most chat
# turns and well under the 8K cap most
# Anthropic-compatible APIs advertise. The Chat /
# Channel layer can ask for more if a specific use
# case needs it.
_MAX_TOKENS_DEFAULT = 1024


def _is_context_length_error(message: str) -> bool:
    """Heuristic — the SDK puts the upstream error text
    into the exception message. Most providers phrase
    context-length overflow as "prompt is too long"
    or "context length exceeded". Keep this loose;
    false positives just fall through to the generic
    LLMError.
    """
    m = message.lower()
    return (
        "context length" in m
        or "prompt is too long" in m
        or "maximum context" in m
        or "context_length" in m
    )


class AnthropicProvider(LLMProvider):
    """Abstract base for Anthropic-API-compatible vendors.

    Subclasses must define three class attributes:

    - ``_BASE_URL``: the upstream API root (no trailing
      slash; the SDK appends ``/v1/messages`` itself).
    - ``_DEFAULT_MODEL``: the model used when the
      caller doesn't pass ``model=``.
    - ``_ERROR_LABEL``: the string shown in error
      messages (e.g. ``"claude"`` / ``"minimax"``) so
      an operator looking at logs can tell which
      vendor failed without grepping the URL.

    Subclasses do **not** need to override ``chat`` or
    ``default_model`` — the shared base implementation
    handles both. The subclass only fixes the
    per-vendor config.
    """

    # --- subclass-overridable config -------------------------

    _BASE_URL: str = ""            # set in subclass
    _DEFAULT_MODEL: str = ""       # set in subclass
    _ERROR_LABEL: str = "anthropic"  # set in subclass

    # --- end subclass-overridable config ---------------------

    def __init__(self, api_key: str, model: str | None = None) -> None:
        if not self._BASE_URL:
            # Defensive — a subclass forgot to set the
            # base URL. Explode here so a typo at the
            # call site is easier to debug.
            raise LLMError(
                f"{type(self).__name__} must declare _BASE_URL"
            )
        super().__init__(api_key, model)
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=self._BASE_URL,
            # Keep timeouts short — the agent loop is
            # the one waiting on this call. If the
            # upstream is slow, the caller (TG bot)
            # gets a clear timeout instead of a hung
            # event loop.
            timeout=30.0,
        )

    def default_model(self) -> str:
        return self._DEFAULT_MODEL

    async def chat(
        self,
        system: str | None,
        messages: list[ChatMessage],
        max_tokens: int = _MAX_TOKENS_DEFAULT,
        tools: list[dict] | None = None,
    ) -> ChatResult:
        # Translate the runtime's flat message list
        # into the SDK's expected shape. ``content`` is
        # a string for plain text turns; when
        # ``content_blocks`` is set (D.16: tool_result
        # echoes or assistant raw-block replays) we
        # pass the structured form so the SDK
        # preserves the block types.
        sdk_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.content_blocks:
                sdk_messages.append({
                    "role": m.role,
                    "content": m.content_blocks,
                })
            else:
                sdk_messages.append({"role": m.role, "content": m.content})
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": sdk_messages,
        }
        if system:
            kwargs["system"] = system
        # ``tools`` is the Anthropic
        # ``[{name, description, input_schema}]`` list.
        # ``None`` / empty means "model can't call any
        # tools" — which is also the default when the
        # agent decides to register zero tools. We
        # don't pass ``tool_choice``; the model decides
        # whether to use a tool on its own.
        if tools:
            kwargs["tools"] = tools

        # Short alias so the error blocks below don't
        # repeat the (potentially long) _ERROR_LABEL
        # attribute lookup.
        label = self._ERROR_LABEL

        try:
            # The SDK's ``messages.create`` is sync.
            # Wrap in to_thread so the FastAPI event
            # loop stays free for other requests while
            # the upstream thinks.
            response = await asyncio.to_thread(
                self._client.messages.create, **kwargs
            )
        except anthropic.AuthenticationError as e:
            raise LLMAuthError(f"{label} auth failed: {e}") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMAuthError(f"{label} permission denied: {e}") from e
        except anthropic.RateLimitError as e:
            raise LLMRateLimitError(f"{label} rate limited: {e}") from e
        except anthropic.APITimeoutError as e:
            raise LLMNetworkError(f"{label} timeout: {e}") from e
        except anthropic.APIConnectionError as e:
            raise LLMNetworkError(f"{label} connection error: {e}") from e
        except anthropic.BadRequestError as e:
            # 400 covers invalid model name, malformed
            # body, and context-length overflow.
            # Inspect the message to split out the
            # context-length case so the caller can
            # react (trim history) rather than treating
            # it as a generic 400.
            if _is_context_length_error(str(e)):
                raise LLMContextLengthError(
                    f"{label} context overflow: {e}"
                ) from e
            raise LLMError(f"{label} bad request: {e}") from e
        except anthropic.APIStatusError as e:
            # Other 4xx / 5xx — treat as transient
            # network-ish.
            raise LLMNetworkError(
                f"{label} status {e.status_code}: {e}"
            ) from e

        # Walk content blocks. All Anthropic-API-
        # compatible vendors carry the same response
        # shape: an ordered list of blocks with a
        # ``type`` discriminator. We extract:
        # - ``text``       → user-facing reply
        # - ``thinking``   → chain-of-thought
        #                    (audit-only, never sent to
        #                    the user)
        # - ``tool_use``   → agent loop dispatches each
        #                    one to the registered tool
        #                    and feeds the result back
        #                    as the next ``user`` turn
        # - everything else → captured in
        #                    ``raw_blocks`` for future
        #                    replay / audit, ignored
        #                    for the immediate reply
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        raw_blocks: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []
        for block in response.content:
            # Pydantic models in the SDK expose
            # ``model_dump`` since 0.30. Fall back to
            # ``__dict__`` if the installed version is
            # older (defensive — the lock pins 0.113 but
            # tests might run on a different env).
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
            elif btype == "tool_use":
                # The SDK gives us a Pydantic model with
                # ``id``, ``name``, ``input`` attrs.
                # Flatten to plain dicts so the rest of
                # the runtime never has to import
                # anthropic types.
                tool_uses.append({
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": dict(getattr(block, "input", {}) or {}),
                })

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
            stop_reason=getattr(response, "stop_reason", None),
            tool_uses=tool_uses,
        )