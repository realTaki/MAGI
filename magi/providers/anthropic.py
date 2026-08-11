"""Anthropic-API 兼容 chat completions 的公共基类。

:class:`magi.providers.claude_code.ClaudeProvider`（Anthropic 自家 API）
和 :class:`magi.providers.minimax.MinimaxProvider`（Minimax 的中国/海外
节点）都继承本类。两个厂商 wire 协议一致（Anthropic Messages API），
差异只有 base_url / 默认模型 / 错误标签。本基类统一处理：

- SDK 客户端构造（带 timeout）
- ``messages.create`` 调用（含流式）
- 错误映射（auth / permission-denied / rate-limit / network /
  context-length / 4xx-5xx）
- 响应拆解（text / thinking / tool_use 提取；其它进 raw_blocks）

子类使用
========

最简单的方式是声明三个类属性（``_BASE_URL`` / ``_DEFAULT_MODEL`` /
``_ERROR_LABEL``），由基类 ``__init__`` 读 ``_BASE_URL`` 构造 SDK
client。

如果需要在运行时切换 base_url（例如 Minimax 的多 region），也可以
``base_url=`` 关键字直接传给构造器，临时覆盖类属性。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from magi.providers._utils import is_context_length_error, safe_dump
from magi.providers.base import LLMProvider, LLMStreamEvent, StreamEventKind
from magi.providers.errors import (
    LLMAuthError,
    LLMContextLengthError,
    LLMError,
    LLMNetworkError,
    LLMRateLimitError,
)

logger = logging.getLogger("magi.providers.anthropic")

_MAX_TOKENS_DEFAULT = 1024


def _wrap_anthropic_error(exc: anthropic.APIError, label: str) -> LLMError:
    """Translate an Anthropic SDK exception into the typed :class:`LLMError`.

    Shared by both :meth:`chat` and :meth:`stream` so the two paths
    produce identical error envelopes (the worker maps ``type(exc).__name__``
    to the operator-facing ``error_code``).
    """
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return LLMAuthError(f"{label} auth/permission failed: {exc}")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMRateLimitError(f"{label} rate limited: {exc}")
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return LLMNetworkError(f"{label} network error: {exc}")
    if isinstance(exc, anthropic.BadRequestError):
        if is_context_length_error(str(exc)):
            return LLMContextLengthError(f"{label} context overflow: {exc}")
        return LLMError(f"{label} bad request: {exc}")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMNetworkError(f"{label} status {getattr(exc, 'status_code', '?')}: {exc}")
    return LLMError(f"{label} error: {exc}")


class AnthropicProvider(LLMProvider):
    """Anthropic-API 兼容厂商的抽象基类。"""

    _BASE_URL: str = ""
    _DEFAULT_MODEL: str = ""
    _ERROR_LABEL: str = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        # ``base_url`` is an explicit override hook so subclasses (e.g.
        # Minimax) don't need to manufacture a fresh subclass per
        # region — they can pass the URL directly. Kept keyword-only.
        url = base_url or self._BASE_URL
        if not url:
            raise LLMError(f"{type(self).__name__} must declare _BASE_URL or pass base_url")
        super().__init__(api_key, model)
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=url,
            timeout=30.0,
        )

    def default_model(self) -> str:
        return self._DEFAULT_MODEL

    async def chat(
        self,
        system: str | None,
        messages: list[dict],
        max_tokens: int = _MAX_TOKENS_DEFAULT,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        sdk_messages = _to_sdk_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": sdk_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        label = self._ERROR_LABEL
        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                **kwargs,
            )
        except anthropic.APIError as exc:
            raise _wrap_anthropic_error(exc, label) from exc

        return _response_to_dict(response, self.model)

    async def stream(
        self,
        system: str | None,
        messages: list[dict],
        *,
        max_tokens: int = _MAX_TOKENS_DEFAULT,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Native Anthropic SDK stream — yields per-delta events.

        Aggregate state (final model + usage + tool_uses) is emitted
        as a final ``usage.updated`` (with model field piggy-backed
        in the payload) so the consumer can rebuild the final dict
        without a second SDK call.

        Thread→async bridge: the SDK stream is sync and must be
        consumed from a worker thread (``asyncio.to_thread``). The
        reader thread hands events to a ``threading.Event``-flavoured
        ``asyncio.Queue`` via :func:`asyncio.run_coroutine_threadsafe`,
        and the main async iterator drains that queue. This keeps
        deltas arriving in order while never blocking the event loop.
        """
        sdk_messages = _to_sdk_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": sdk_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        loop = asyncio.get_running_loop()
        event_queue: asyncio.Queue[LLMStreamEvent] = asyncio.Queue()

        async def _yield(event: LLMStreamEvent) -> None:
            await event_queue.put(event)

        def _emit(kind: StreamEventKind, payload: dict[str, Any]) -> None:
            asyncio.run_coroutine_threadsafe(
                _yield(LLMStreamEvent(kind, payload)),
                loop,
            ).result()

        # Tool-call buffers keyed by the slot index Anthropic assigns
        # in ``content_block_start`` — the SDK's ``input_json_delta``
        # events don't carry the tool id, only the index, so the
        # index is the only stable handle across a parallel call burst.
        tool_buffers_by_slot: dict[int, dict[str, Any]] = {}
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        usage_dict: dict[str, Any] | None = None
        model_name = self.model
        stop_reason: str | None = None
        raw_blocks: list[dict[str, Any]] = []

        def _read() -> None:
            nonlocal usage_dict, model_name, stop_reason
            with self._client.messages.stream(**kwargs) as stream:
                for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        btype = getattr(block, "type", None)
                        if btype == "tool_use":
                            slot_idx = getattr(event, "index", None)
                            if not isinstance(slot_idx, int):
                                slot_idx = len(tool_buffers_by_slot)
                            tool_buffers_by_slot[slot_idx] = {
                                "id": getattr(block, "id", ""),
                                "name": getattr(block, "name", ""),
                                "input_json": "",
                                "input": {},
                            }
                    elif etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", "")
                        if dtype == "text_delta":
                            chunk = getattr(delta, "text", "")
                            if chunk:
                                text_parts.append(chunk)
                                _emit("text.delta", {"text": chunk})
                        elif dtype == "thinking_delta":
                            thinking_parts.append(getattr(delta, "thinking", "") or "")
                        elif dtype in {"input_json_delta", "json_delta"}:
                            slot_idx = getattr(event, "index", None)
                            partial = getattr(delta, "partial_json", "") or ""
                            slot = (
                                tool_buffers_by_slot.get(slot_idx)
                                if isinstance(slot_idx, int)
                                else None
                            )
                            if slot is not None:
                                slot["input_json"] += partial
                            else:
                                # Delta arrived before / without a
                                # matching ``content_block_start`` —
                                # extremely rare but tolerated: stash
                                # under a synthetic slot so the
                                # arguments aren't lost.
                                key = (
                                    slot_idx
                                    if isinstance(slot_idx, int)
                                    else len(tool_buffers_by_slot)
                                )
                                tool_buffers_by_slot[key] = {
                                    "id": "",
                                    "name": "",
                                    "input_json": partial,
                                    "input": {},
                                }
                    elif etype == "content_block_stop":
                        pass
                    elif etype == "message_delta":
                        stop_reason = (
                            getattr(
                                getattr(event, "delta", None),
                                "stop_reason",
                                None,
                            )
                            or stop_reason
                        )
                    elif etype == "message_start":
                        msg = getattr(event, "message", None)
                        if msg is not None:
                            model_name = getattr(msg, "model", model_name) or model_name
                            u = getattr(msg, "usage", None)
                            if u is not None:
                                usage_dict = safe_dump(u)
                    elif etype == "message_stop":
                        pass

                final = stream.get_final_message()
                # Backfill model + usage from final message.
                model_name = getattr(final, "model", model_name) or model_name
                u = getattr(final, "usage", None)
                if u is not None:
                    usage_dict = safe_dump(u)
                stop_reason = getattr(final, "stop_reason", None) or stop_reason
                raw_blocks.extend(_collect_raw_blocks(final))

        try:
            await asyncio.to_thread(_read)
        except anthropic.APIError as exc:
            raise _wrap_anthropic_error(exc, self._ERROR_LABEL) from exc

        # Parse accumulated tool args.
        tool_uses: list[dict[str, Any]] = []
        for slot_idx in sorted(tool_buffers_by_slot):
            slot = tool_buffers_by_slot[slot_idx]
            args_raw = slot.get("input_json") or ""
            parsed: Any = {}
            if args_raw:
                try:
                    parsed = json.loads(args_raw)
                except json.JSONDecodeError:
                    parsed = {}
            tool_uses.append(
                {
                    "id": slot.get("id") or "",
                    "name": slot.get("name") or "",
                    "input": parsed if isinstance(parsed, dict) else {},
                }
            )

        # Deltas concatenate without separator (SDK guarantees they
        # already carry whatever spacing the model intended). The
        # non-streaming ``_response_to_dict`` joins full text *blocks*
        # with a newline — different unit, different rule.
        text = "".join(text_parts).strip() or "(empty reply)"
        thinking = "".join(p for p in thinking_parts if p).strip() or None
        # Single trailing usage.updated carrying everything the
        # consumer needs to rebuild the final dict.
        yield LLMStreamEvent(
            "usage.updated",
            {
                "model": model_name,
                "stop_reason": stop_reason,
                "usage": usage_dict or {},
                "tool_uses": tool_uses,
                "text": text,
                "thinking": thinking,
                "raw_blocks": raw_blocks,
            },
        )

        # Drain anything the SDK emitted after we built the terminal
        # payload (e.g. trailing text deltas). The queue is bounded by
        # the SDK stream lifetime so this loop is cheap.
        while not event_queue.empty():
            trailing = event_queue.get_nowait()
            if trailing.kind == "usage.updated":
                continue  # we already emitted ours above
            yield trailing

    # Compatibility alias — some callers historically used
    # ``AnthropicProvider._response_to_result``.  The implementation
    # now lives at module scope as :func:`_response_to_dict`.
    @staticmethod
    def _response_to_result(response: Any, default_model: str) -> dict[str, Any]:
        return _response_to_dict(response, default_model)


# ---------------------------------------------------------------------------
# helpers (module-scope so subclasses and tests can reuse)
# ---------------------------------------------------------------------------


def _to_sdk_messages(messages: list[dict]) -> list[dict[str, Any]]:
    """Translate the runtime's flat message list into the SDK's shape.

    Messages carry an optional ``content_blocks`` field for the cases
    where plain text isn't enough (tool_result echoes, assistant
    raw-block replays). When present we pass the structured form so
    the SDK preserves the block types.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in {"user", "assistant"}:
            continue
        blocks = m.get("content_blocks")
        if blocks:
            out.append({"role": role, "content": list(blocks)})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def _response_to_dict(response: Any, default_model: str) -> dict[str, Any]:
    """Translate a non-streaming SDK response into the canonical dict.

    Returns ``{text, thinking, tool_uses, raw_blocks, model, usage,
    stop_reason}``. ``text`` is never empty: if the model produced
    only thinking blocks, it falls back to ``"(empty reply)"``.

    Multiple text *blocks* (not deltas) are joined with a newline —
    each block is a distinct paragraph in the SDK output, so a
    separator is appropriate.
    """
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    raw_blocks: list[dict[str, Any]] = []
    tool_uses: list[dict[str, Any]] = []
    for block in getattr(response, "content", []) or []:
        dumped = safe_dump(block) or {"type": getattr(block, "type", "unknown")}
        raw_blocks.append(dumped)
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif btype == "thinking":
            thinking_parts.append(getattr(block, "thinking", "") or "")
        elif btype == "tool_use":
            tool_uses.append(
                {
                    "id": getattr(block, "id", "") or "",
                    "name": getattr(block, "name", "") or "",
                    "input": dict(getattr(block, "input", {}) or {}),
                }
            )

    usage = safe_dump(getattr(response, "usage", None))
    text = "\n".join(p for p in text_parts if p).strip()
    thinking = "\n".join(p for p in thinking_parts if p).strip() or None

    return {
        "text": text or "(empty reply)",
        "thinking": thinking,
        "tool_uses": tool_uses,
        "raw_blocks": raw_blocks,
        "model": getattr(response, "model", default_model) or default_model,
        "usage": usage,
        "stop_reason": getattr(response, "stop_reason", None),
    }


def _collect_raw_blocks(response: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in getattr(response, "content", []) or []:
        dumped = safe_dump(block) or {"type": getattr(block, "type", "unknown")}
        blocks.append(dumped)
    return blocks
