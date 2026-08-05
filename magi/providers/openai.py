"""OpenAI chat-completions provider.

Implements :class:`magi.providers.provider.LLMProvider` against
the official OpenAI Chat Completions wire format. Subclasses
:mod:`magi.providers.anthropic` does **not** fit here because
the request/response shapes differ:

  - system prompt lives at the front of the ``messages`` list
    (``role: system``) rather than as a top-level ``system``
    field.
  - tool definitions are wrapped in
    ``{type: "function", function: {name, description, parameters}}``
    instead of the flat Anthropic ``{name, description, input_schema}``
    list.
  - tool calls are returned inside the assistant message as
    ``tool_calls`` (parallel-capable) and the corresponding
    tool results are returned in subsequent ``role: tool``
    messages bound by ``tool_call_id`` — not as
    ``content_blocks`` on a user turn.

The class bridges those shapes so the agent loop, durable
worker, tool registry, and audit rows keep using the
provider-neutral :class:`ChatMessage` /
:class:`ChatResult` contract from
:mod:`magi.providers.provider`. Wire-format conversion and
error mapping live here only; callers see an ``LLMProvider``.

The official OpenAI endpoint is used; the SDK defaults are
adequate. The MAGI configuration still only carries
``provider`` / ``api_key`` / ``model`` (per the factory), so
this module intentionally does not accept a ``base_url`` —
adding one would be a schema-level change and isn't part
of the v0 scope.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import openai
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
    RateLimitError,
)

from magi.providers.errors import (
    LLMAuthError,
    LLMContextLengthError,
    LLMError,
    LLMNetworkError,
    LLMRateLimitError,
)
from magi.providers.provider import (
    ChatMessage,
    ChatResult,
    LLMProvider,
    LLMStreamEvent,
)

logger = logging.getLogger("magi.agent.llm.openai")

# Cap on a single reply. Matches the Anthropic adapter so the
# agent loop treats both providers symmetrically. Channels that
# need more can pass ``max_tokens`` explicitly.
_MAX_TOKENS_DEFAULT = 1024

# Default model when the operator hasn't picked one in the
# MAGIC row. Picked from the current OpenAI general-availability
# line; operators are free to override per-runtime.
_DEFAULT_MODEL = "gpt-4o-mini"

# Provider id surfaced to audit / hooks. Lowercase, hyphenated,
# matches the rest of :mod:`magi.providers.factory`.
_PROVIDER_NAME = "openai"

# Substrings the OpenAI SDK / upstream put into
# ``BadRequestError`` when the input blows past the model's
# context window. Mirrors the Anthropic heuristic in
# :mod:`magi.providers.anthropic`.
_CONTEXT_LENGTH_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "prompt is too long",
    "reduce the length",
    "tokens must be reduced",
)


def _is_context_length_error(message: str) -> bool:
    m = message.lower()
    return any(marker in m for marker in _CONTEXT_LENGTH_MARKERS)


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Translate MAGI tool schemas into OpenAI function schemas.

    The agent loop + tool catalog already emit
    ``[{name, description, input_schema}]`` (Anthropic shape).
    OpenAI expects each entry wrapped in
    ``{type: "function", function: {name, description, parameters}}``.
    """
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise LLMError(f"OpenAI provider received non-dict tool: {tool!r}")
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            converted.append(tool)
            continue
        name = tool.get("name")
        if not name:
            raise LLMError("OpenAI provider received a tool without a name")
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
        )
    return converted


def _convert_messages(
    system: str | None,
    messages: list[ChatMessage],
) -> list[dict[str, Any]]:
    """Translate MAGI ``ChatMessage`` history to OpenAI messages.

    Mapping:

    - system prompt → first message with ``role: system``.
    - plain user/assistant text → ``{role, content}``.
    - assistant ``content_blocks`` (the prior turn's
      ``raw_blocks``) → assistant message with
      ``tool_calls`` reconstructed from the original
      ``tool_use`` blocks. The original OpenAI ``id`` is
      preserved so the next round of tool results bind to
      the same call.
    - user ``content_blocks`` containing ``tool_result``
      blocks → one ``role: tool`` message per result, each
      carrying the matching ``tool_call_id``. If a user
      turn also had a non-empty ``content`` it is sent as
      a sibling text message so the model sees both.
    """
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for message in messages:
        if message.role == "assistant":
            if message.content_blocks:
                # Replay path — the prior turn's raw_blocks
                # round-trip back to the model. Pull tool_use
                # blocks out as parallel tool_calls; carry
                # text/thinking into the same message so
                # OpenAI sees the same shape it originally
                # produced.
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for block in message.content_blocks:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text" and block.get("text"):
                        text_parts.append(str(block["text"]))
                    elif btype == "tool_use":
                        arguments = block.get("input", {}) or {}
                        if not isinstance(arguments, dict):
                            arguments = {"value": arguments}
                        tool_calls.append(
                            {
                                "id": str(block.get("id") or ""),
                                "type": "function",
                                "function": {
                                    "name": str(block.get("name") or ""),
                                    "arguments": json.dumps(arguments, ensure_ascii=False),
                                },
                            }
                        )
                assistant_msg: dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    assistant_msg["content"] = "\n".join(text_parts)
                else:
                    assistant_msg["content"] = ""
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                out.append(assistant_msg)
            else:
                out.append({"role": "assistant", "content": message.content or ""})
            continue

        # user role
        if message.content_blocks:
            # Tool results — each ``tool_result`` block becomes
            # its own ``role: tool`` message so OpenAI can
            # bind the id. We emit any accompanying text as
            # a separate user message immediately after.
            for block in message.content_blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    # Unknown block type from a future
                    # transport — keep it in the transcript
                    # so audit rows still match.
                    out.append({"role": "user", "content": json.dumps(block, ensure_ascii=False)})
                    continue
                content = block.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                tool_msg: dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id") or block.get("id") or ""),
                    "content": content,
                }
                out.append(tool_msg)
            if message.content:
                out.append({"role": "user", "content": message.content})
        else:
            out.append({"role": "user", "content": message.content or ""})

    return out


def _convert_usage(usage_obj: Any) -> dict[str, Any] | None:
    """Normalise OpenAI usage into MAGI's ``{input_tokens, output_tokens, ...}`` shape.

    Falls back to a defensive attr walk because some SDK
    builds expose Pydantic v1 models (``__dict__``) and
    others expose v2 (``model_dump``). The MAGI contract
    reads ``input_tokens`` / ``output_tokens``; everything
    else (cached tokens, reasoning tokens) is retained so
    audit rows can still see them.
    """
    if usage_obj is None:
        return None
    if hasattr(usage_obj, "model_dump"):
        try:
            raw = usage_obj.model_dump()
        except Exception:
            raw = None
    elif hasattr(usage_obj, "to_dict"):
        raw = usage_obj.to_dict()
    elif hasattr(usage_obj, "__dict__"):
        raw = dict(usage_obj.__dict__)
    else:
        raw = None
    if raw is None:
        return None
    out: dict[str, Any] = {}
    if "prompt_tokens" in raw and raw["prompt_tokens"] is not None:
        out["input_tokens"] = int(raw["prompt_tokens"])
    if "completion_tokens" in raw and raw["completion_tokens"] is not None:
        out["output_tokens"] = int(raw["completion_tokens"])
    if "total_tokens" in raw and raw["total_tokens"] is not None:
        out["total_tokens"] = int(raw["total_tokens"])
    for key, value in raw.items():
        if key in {"prompt_tokens", "completion_tokens", "total_tokens"}:
            continue
        if value is None:
            continue
        out[key] = value
    return out or None


def _normalize_finish_reason(reason: Any) -> str | None:
    """Map OpenAI's ``finish_reason`` to the values MAGI's agent loop branches on.

    The provider contract only branches on ``"end_turn"``
    (terminal text reply) and ``"tool_use"`` (assistant
    emitted tool_calls). Everything else falls through to
    a best-effort label so audit rows still record what
    upstream said.
    """
    if reason is None:
        return None
    value = str(reason).strip().lower()
    if value in {"stop"}:
        return "end_turn"
    if value in {"tool_calls", "function_call"}:
        return "tool_use"
    if value in {"length", "max_tokens"}:
        return "max_tokens"
    if value in {"content_filter", "safety"}:
        return "end_turn"
    if value in {"end_turn"}:
        return "end_turn"
    return value or None


def _extract_text(message: Any) -> str:
    """Pull plain text out of an OpenAI message or chunk delta.

    Some SDK versions return ``content`` as ``None`` when the
    model only emitted tool calls; treat that as the empty
    string so downstream code doesn't special-case ``None``.
    """
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # Some compatible endpoints return a list of content
    # parts. Concatenate any text-shaped entries so the
    # runtime never sees the list type.
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
        else:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _extract_tool_calls(message: Any) -> list[Any]:
    return list(getattr(message, "tool_calls", None) or [])


def _arguments_from_tool_call(call: Any) -> Any:
    """Decode OpenAI's JSON-stringified arguments.

    Some SDK builds return ``arguments`` as a Pydantic
    model; some return a raw string. The wire format
    mandates a JSON string, so the latter is the common
    case. Return whatever object (dict or string) is on
    hand and let the caller decide.
    """
    fn = getattr(call, "function", None)
    args = getattr(fn, "arguments", None) if fn is not None else None
    return args


def _parse_arguments(arguments: Any, *, call_id: str) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError as exc:
            # Defensive: upstream occasionally returns truncated
            # JSON for partial tool calls. Don't 500 the agent
            # loop; surface an empty input so the loop can
            # continue and the tool will run with no args.
            logger.warning(
                "openai provider: tool_call %s had non-JSON arguments (%s); using empty dict",
                call_id, exc,
            )
            return {}
        if isinstance(decoded, dict):
            return decoded
        return {"value": decoded}
    return {"value": arguments}


def _build_result(
    *,
    message: Any,
    raw_response: Any,
) -> ChatResult:
    """Translate a single OpenAI message into a :class:`ChatResult`."""
    text = _extract_text(message)
    thinking: str | None = None
    reasoning_details = getattr(message, "reasoning_details", None)
    if reasoning_details:
        parts: list[str] = []
        for detail in reasoning_details:
            if isinstance(detail, dict):
                text_part = detail.get("text")
                if text_part:
                    parts.append(str(text_part))
            else:
                text_part = getattr(detail, "text", None)
                if text_part:
                    parts.append(str(text_part))
        if parts:
            thinking = "\n".join(parts)

    tool_uses: list[dict[str, Any]] = []
    raw_blocks: list[dict[str, Any]] = []
    for call in _extract_tool_calls(message):
        call_id = str(getattr(call, "id", "") or "")
        fn = getattr(call, "function", None)
        name = str(getattr(fn, "name", "") or "") if fn is not None else ""
        arguments = _arguments_from_tool_call(call)
        parsed = _parse_arguments(arguments, call_id=call_id)
        tool_uses.append({"id": call_id, "name": name, "input": parsed})
        raw_blocks.append(
            {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": parsed,
            }
        )

    if text:
        raw_blocks.insert(0, {"type": "text", "text": text})
    if thinking:
        raw_blocks.append({"type": "thinking", "thinking": thinking})

    return ChatResult(
        text=text or "(empty reply)",
        thinking=thinking,
        model=getattr(raw_response, "model", "") or "",
        usage=_convert_usage(getattr(raw_response, "usage", None)),
        raw_blocks=raw_blocks,
        stop_reason=_normalize_finish_reason(getattr(raw_response, "choices", [None])[0].finish_reason if getattr(raw_response, "choices", None) else None) if raw_response is not None else None,
        tool_uses=tool_uses,
    )


def _wrap_exception(exc: openai.OpenAIError) -> LLMError:
    label = _PROVIDER_NAME
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return LLMAuthError(f"{label} auth failed: {exc}")
    if isinstance(exc, RateLimitError):
        return LLMRateLimitError(f"{label} rate limited: {exc}")
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return LLMNetworkError(f"{label} network error: {exc}")
    if isinstance(exc, BadRequestError):
        if _is_context_length_error(str(exc)):
            return LLMContextLengthError(f"{label} context overflow: {exc}")
        return LLMError(f"{label} bad request: {exc}")
    if isinstance(exc, APIStatusError):
        return LLMNetworkError(f"{label} status {getattr(exc, 'status_code', '?')}: {exc}")
    return LLMError(f"{label} error: {exc}")


class OpenAIProvider(LLMProvider):
    """Provider that talks to the official OpenAI chat-completions endpoint.

    The constructor signature mirrors
    :meth:`magi.providers.anthropic.AnthropicProvider.__init__`
    so the factory can instantiate both with the same
    ``api_key=`` / ``model=`` kwargs it already collects
    from the MAGIC row. Extra keyword plumbing (organisation
    id, proxy endpoints) is intentionally **not** exposed:
    the configuration model only carries provider, API key,
    and model name today.
    """

    name = _PROVIDER_NAME

    def __init__(self, api_key: str, model: str | None = None) -> None:
        super().__init__(api_key, model)
        self._client = AsyncOpenAI(
            api_key=api_key,
            # 30s matches the Anthropic adapter — the agent
            # loop is the one waiting on this call, so a
            # hung upstream should fail fast.
            timeout=30.0,
        )

    def default_model(self) -> str:
        return _DEFAULT_MODEL

    async def chat(
        self,
        system: str | None,
        messages: list[ChatMessage],
        max_tokens: int = _MAX_TOKENS_DEFAULT,
        tools: list[dict] | None = None,
    ) -> ChatResult:
        sdk_messages = _convert_messages(system, messages)
        sdk_tools = _convert_tools(tools)
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": sdk_messages,
        }
        if sdk_tools:
            params["tools"] = sdk_tools

        try:
            response = await self._client.chat.completions.create(**params)
        except openai.OpenAIError as exc:
            raise _wrap_exception(exc) from exc

        if not getattr(response, "choices", None):
            raise LLMError("openai provider: response carried no choices")
        message = response.choices[0].message
        return _build_result(message=message, raw_response=response)

    async def stream(
        self,
        system: str | None,
        messages: list[ChatMessage],
        *,
        max_tokens: int = _MAX_TOKENS_DEFAULT,
        tools: list[dict] | None = None,
        on_event: Callable[[LLMStreamEvent], Any],
    ) -> ChatResult:
        """Stream chat-completions deltas, mirroring the Anthropic adapter.

        Emits provider-neutral events for text and tool-call
        argument deltas, and the final usage update. The
        returned :class:`ChatResult` has the same shape as
        :meth:`chat` so callers never need to know whether
        they got a streamed or non-streamed reply.
        """
        sdk_messages = _convert_messages(system, messages)
        sdk_tools = _convert_tools(tools)
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": sdk_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if sdk_tools:
            params["tools"] = sdk_tools

        # Local aggregation state.
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_call_buffers: dict[int, dict[str, Any]] = {}
        model_name: str = self.model
        final_usage: Any = None
        finish_reason: Any = None

        try:
            stream = await self._client.chat.completions.create(**params)
            async for chunk in stream:
                model_name = getattr(chunk, "model", None) or model_name
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    final_usage = chunk_usage
                for choice in getattr(chunk, "choices", None) or ():
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    finish_reason = getattr(choice, "finish_reason", None) or finish_reason
                    delta_text = _extract_text(delta)
                    if delta_text:
                        text_parts.append(delta_text)
                        await on_event(LLMStreamEvent("text.delta", {"text": delta_text}))
                    reasoning_details = getattr(delta, "reasoning_details", None)
                    if reasoning_details:
                        for detail in reasoning_details:
                            if isinstance(detail, dict):
                                text_part = detail.get("text")
                            else:
                                text_part = getattr(detail, "text", None)
                            if text_part:
                                thinking_parts.append(str(text_part))
                    for call in _extract_tool_calls(delta):
                        idx = getattr(call, "index", None)
                        slot_index = idx if isinstance(idx, int) else len(tool_call_buffers)
                        slot = tool_call_buffers.setdefault(
                            slot_index,
                            {"id": "", "name": "", "arguments": ""},
                        )
                        new_id = getattr(call, "id", None)
                        if new_id:
                            slot["id"] = str(new_id)
                        fn = getattr(call, "function", None)
                        if fn is not None:
                            new_name = getattr(fn, "name", None)
                            if new_name:
                                slot["name"] = str(new_name)
                            new_args = getattr(fn, "arguments", None)
                            if new_args:
                                slot["arguments"] += str(new_args)
                                await on_event(
                                    LLMStreamEvent(
                                        "tool_arguments.delta",
                                        {
                                            "partial_json": str(new_args),
                                            "id": slot["id"],
                                            "name": slot["name"],
                                        },
                                    )
                                )
        except openai.OpenAIError as exc:
            raise _wrap_exception(exc) from exc

        # Emit the final usage once it has arrived. The
        # chunk's ``usage`` field is the canonical source.
        if final_usage is not None:
            payload = _convert_usage(final_usage) or {}
            await on_event(LLMStreamEvent("usage.updated", dict(payload)))

        text = "".join(text_parts)
        thinking = "\n".join(p for p in thinking_parts if p).strip() or None
        tool_uses: list[dict[str, Any]] = []
        raw_blocks: list[dict[str, Any]] = []
        for slot_index in sorted(tool_call_buffers):
            slot = tool_call_buffers[slot_index]
            parsed = _parse_arguments(slot["arguments"], call_id=slot["id"])
            tool_uses.append({"id": slot["id"], "name": slot["name"], "input": parsed})
            raw_blocks.append(
                {
                    "type": "tool_use",
                    "id": slot["id"],
                    "name": slot["name"],
                    "input": parsed,
                }
            )
        if text:
            raw_blocks.insert(0, {"type": "text", "text": text})
        if thinking:
            raw_blocks.append({"type": "thinking", "thinking": thinking})

        return ChatResult(
            text=text or "(empty reply)",
            thinking=thinking,
            model=model_name or self.model,
            usage=_convert_usage(final_usage),
            raw_blocks=raw_blocks,
            stop_reason=_normalize_finish_reason(finish_reason),
            tool_uses=tool_uses,
        )


__all__ = ["OpenAIProvider"]
