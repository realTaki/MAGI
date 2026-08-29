"""OpenAI chat-completions provider。

直接继承 :class:`LLMProvider`，不复用 :mod:`anthropic`，因为 OpenAI
的请求 / 响应形状不一致：

- system prompt 落在 ``messages`` 列表头（``role: system``），不是
  顶层 ``system`` 字段。
- tool 定义套 ``{type: "function", function: {name, description,
  parameters}}``，不是 Anthropic 的扁平 ``{name, description,
  input_schema}``。
- tool call 在 assistant message 内的 ``tool_calls`` 数组里；tool
  结果用 ``role: tool`` + ``tool_call_id`` 绑定。

本文件把上述 4 处翻译全部包掉，对外保持 ``LLMProvider`` 接口。

Wire-format 转换和错误映射只在这里；调用方看到的就是 dict 形态的
``LLMProvider.chat()`` 返回值。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
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

from magi.providers._utils import is_context_length_error, safe_dump
from magi.providers.base import LLMProvider, LLMStreamEvent
from magi.providers.errors import (
    LLMAuthError,
    LLMContextLengthError,
    LLMError,
    LLMNetworkError,
    LLMRateLimitError,
)

logger = logging.getLogger("magi.providers.openai")

_MAX_TOKENS_DEFAULT = 1024
_DEFAULT_MODEL = "gpt-5.6-terra"
_PROVIDER_NAME = "openai"


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Translate MAGI tool schemas into OpenAI function schemas."""
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise LLMError(f"openai provider received non-dict tool: {tool!r}")
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            converted.append(tool)
            continue
        name = tool.get("name")
        if not name:
            raise LLMError("openai provider received a tool without a name")
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
    messages: list[dict],
) -> list[dict[str, Any]]:
    """Translate MAGI ``list[dict]`` history to OpenAI messages."""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            blocks = message.get("content_blocks")
            if blocks:
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for block in blocks:
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
                out.append({"role": "assistant", "content": message.get("content") or ""})
            continue

        # user role
        blocks = message.get("content_blocks")
        if blocks:
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
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
            if message.get("content"):
                out.append({"role": "user", "content": message.get("content")})
        else:
            out.append({"role": "user", "content": message.get("content") or ""})

    return out


def _convert_usage(usage_obj: Any) -> dict[str, Any] | None:
    """Translate an OpenAI usage object into the canonical token envelope.

    Renames ``prompt_tokens``/``completion_tokens``/``total_tokens`` to
    the wire-format keys the worker reads off ``CallLLMResult``;
    preserves any extra metadata (``prompt_tokens_details`` etc.)
    verbatim.
    """
    raw = safe_dump(usage_obj)
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
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
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
    fn = getattr(call, "function", None)
    return getattr(fn, "arguments", None) if fn is not None else None


def _parse_arguments(arguments: Any, *, call_id: str) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError as exc:
            logger.warning(
                "openai provider: tool_call %s had non-JSON arguments (%s); using empty dict",
                call_id,
                exc,
            )
            return {}
        if isinstance(decoded, dict):
            return decoded
        return {"value": decoded}
    return {"value": arguments}


def _wrap_exception(exc: openai.OpenAIError) -> LLMError:
    label = _PROVIDER_NAME
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return LLMAuthError(f"{label} auth failed: {exc}")
    if isinstance(exc, RateLimitError):
        return LLMRateLimitError(f"{label} rate limited: {exc}")
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return LLMNetworkError(f"{label} network error: {exc}")
    if isinstance(exc, BadRequestError):
        if is_context_length_error(str(exc)):
            return LLMContextLengthError(f"{label} context overflow: {exc}")
        return LLMError(f"{label} bad request: {exc}")
    if isinstance(exc, APIStatusError):
        return LLMNetworkError(f"{label} status {getattr(exc, 'status_code', '?')}: {exc}")
    return LLMError(f"{label} error: {exc}")


class OpenAIProvider(LLMProvider):
    """官方 OpenAI chat-completions endpoint。"""

    name = _PROVIDER_NAME

    def __init__(self, api_key: str, model: str | None = None) -> None:
        super().__init__(api_key, model)
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=30.0,
        )

    def default_model(self) -> str:
        return _DEFAULT_MODEL

    async def chat(
        self,
        system: str | None,
        messages: list[dict],
        max_tokens: int = _MAX_TOKENS_DEFAULT,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
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
        return _message_to_dict(message=message, raw_response=response)

    async def stream(
        self,
        system: str | None,
        messages: list[dict],
        *,
        max_tokens: int = _MAX_TOKENS_DEFAULT,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream chat-completions deltas; emit one final ``usage.updated``
        carrying everything needed to rebuild the final dict."""
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
                        yield LLMStreamEvent("text.delta", {"text": delta_text})
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
                                yield LLMStreamEvent(
                                    "tool_arguments.delta",
                                    {
                                        "partial_json": str(new_args),
                                        "id": slot["id"],
                                        "name": slot["name"],
                                    },
                                )
        except openai.OpenAIError as exc:
            raise _wrap_exception(exc) from exc

        text = "".join(text_parts)
        # Thinking deltas arrive as discrete reasoning chunks; concatenate
        # without separator (mirrors text.delta handling — deltas don't
        # carry inter-chunk spacing).
        thinking = "".join(p for p in thinking_parts if p).strip() or None
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

        yield LLMStreamEvent(
            "usage.updated",
            {
                "model": model_name or self.model,
                "stop_reason": _normalize_finish_reason(finish_reason),
                "usage": _convert_usage(final_usage) or {},
                "tool_uses": tool_uses,
                "text": text or "(empty reply)",
                "thinking": thinking,
                "raw_blocks": raw_blocks,
            },
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _message_to_dict(*, message: Any, raw_response: Any) -> dict[str, Any]:
    """Translate one OpenAI assistant message into the canonical dict."""
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
        raw_blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": parsed})

    if text:
        raw_blocks.insert(0, {"type": "text", "text": text})
    if thinking:
        raw_blocks.append({"type": "thinking", "thinking": thinking})

    finish_reason: Any = None
    if raw_response is not None:
        choices = getattr(raw_response, "choices", None)
        if choices:
            finish_reason = getattr(choices[0], "finish_reason", None)

    return {
        "text": text or "(empty reply)",
        "thinking": thinking,
        "tool_uses": tool_uses,
        "raw_blocks": raw_blocks,
        "model": getattr(raw_response, "model", "") or "",
        "usage": _convert_usage(getattr(raw_response, "usage", None)),
        "stop_reason": _normalize_finish_reason(finish_reason),
    }


__all__ = ["OpenAIProvider"]
