"""Anthropic-API 兼容 chat completions 的公共基类。

:class:`magi.providers.claude_code.ClaudeProvider`（Anthropic 自家 API）
和 :class:`magi.providers.minimax.MinimaxProvider`（Minimax 的中国/海外
节点）都继承本类。两个厂商 wire 协议一致（Anthropic Messages API），
差异只有 base_url / 默认模型 / 错误标签。本基类统一处理：

- SDK 客户端构造（带 timeout）
- ``messages.create`` 调用
- 错误映射（auth / rate-limit / network / context-length / 4xx-5xx）
- 响应拆解（text / thinking / tool_use 提取；其它进 raw_blocks）

子类只需声明三个类属性（``_BASE_URL`` / ``_DEFAULT_MODEL`` /
``_ERROR_LABEL``）就够了。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from magi.providers.errors import (
    LLMContextLengthError,
    LLMError,
    LLMAuthError,
    LLMNetworkError,
    LLMRateLimitError,
)
from magi.providers.base import LLMProvider, LLMStreamEvent

logger = logging.getLogger("magi.agent.llm.anthropic")

_MAX_TOKENS_DEFAULT = 1024


def _is_context_length_error(message: str) -> bool:
    """启发式判断 SDK 异常消息是否提示上下文超限。

    SDK 把上游错误文本塞进 exception message；多数厂商用
    "prompt is too long" / "context length exceeded" 等措辞。
    误判只回退到通用 LLMError，影响有限。
    """
    m = message.lower()
    return (
        "context length" in m
        or "prompt is too long" in m
        or "maximum context" in m
        or "context_length" in m
    )


class AnthropicProvider(LLMProvider):
    """Anthropic-API 兼容厂商的抽象基类。"""

    _BASE_URL: str = ""
    _DEFAULT_MODEL: str = ""
    _ERROR_LABEL: str = "anthropic"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        if not self._BASE_URL:
            raise LLMError(
                f"{type(self).__name__} must declare _BASE_URL"
            )
        super().__init__(api_key, model)
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=self._BASE_URL,
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
            if _is_context_length_error(str(e)):
                raise LLMContextLengthError(
                    f"{label} context overflow: {e}"
                ) from e
            raise LLMError(f"{label} bad request: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMNetworkError(
                f"{label} status {e.status_code}: {e}"
            ) from e

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

        # Per-tool-call buffer (Anthropic streams arguments as
        # incremental JSON fragments; we accumulate per-id and emit
        # one final usage.updated with the tool_use snapshot).
        tool_buffers: dict[str, dict[str, Any]] = {}
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        usage_dict: dict[str, Any] | None = None
        model_name = self.model
        stop_reason: str | None = None
        raw_blocks: list[dict[str, Any]] = []

        def _emit(kind: str, payload: dict[str, Any]) -> None:
            asyncio.run_coroutine_threadsafe(
                _yield(LLMStreamEvent(kind, payload)), loop,
            ).result()

        # Bridge: the SDK stream is sync (driven from a worker thread);
        # we collect deltas into thread-local buffers and let the async
        # iterator emit them via run_coroutine_threadsafe.
        def _read() -> None:
            nonlocal usage_dict, model_name, stop_reason
            with self._client.messages.stream(**kwargs) as stream:
                for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        btype = getattr(block, "type", None)
                        if btype == "tool_use":
                            tool_id = getattr(block, "id", "")
                            tool_buffers[tool_id] = {
                                "id": tool_id,
                                "name": getattr(block, "name", ""),
                                "input_json": "",
                                "input": {},
                            }
                    elif etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", "")
                        if dtype == "text_delta":
                            chunk = getattr(delta, "text", "")
                            text_parts.append(chunk)
                            _emit("text.delta", {"text": chunk})
                        elif dtype == "thinking_delta":
                            thinking_parts.append(getattr(delta, "thinking", ""))
                        elif dtype in {"input_json_delta", "json_delta"}:
                            tid = getattr(event, "index", None)
                            partial = getattr(delta, "partial_json", "")
                            # Map slot index → tool_id lazily. We don't
                            # have the id here, so the consumer rebinds
                            # via the final usage payload.
                            for slot in tool_buffers.values():
                                if slot.get("_slot") == tid:
                                    slot["input_json"] += partial
                                    break
                            else:
                                # New slot
                                key = f"slot-{tid}"
                                tool_buffers[key] = {
                                    "id": "",
                                    "name": "",
                                    "input_json": partial,
                                    "input": {},
                                    "_slot": tid,
                                }
                    elif etype == "content_block_stop":
                        pass
                    elif etype == "message_delta":
                        stop_reason = getattr(
                            getattr(event, "delta", None), "stop_reason", None,
                        ) or stop_reason
                    elif etype == "message_start":
                        msg = getattr(event, "message", None)
                        if msg is not None:
                            model_name = getattr(msg, "model", model_name) or model_name
                            u = getattr(msg, "usage", None)
                            if u is not None:
                                usage_dict = _dump(u)
                    elif etype == "message_stop":
                        pass

                final = stream.get_final_message()
                # Backfill model + usage from final message.
                model_name = getattr(final, "model", model_name) or model_name
                u = getattr(final, "usage", None)
                if u is not None:
                    usage_dict = _dump(u)
                stop_reason = getattr(final, "stop_reason", None) or stop_reason
                raw_blocks.extend(_collect_raw_blocks(final))

        try:
            await asyncio.to_thread(_read)
        except anthropic.AuthenticationError as exc:
            raise LLMAuthError(f"{self._ERROR_LABEL} auth failed: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError(f"{self._ERROR_LABEL} rate limited: {exc}") from exc
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise LLMNetworkError(f"{self._ERROR_LABEL} stream failed: {exc}") from exc

        # Parse accumulated tool args.
        import json as _json

        tool_uses: list[dict[str, Any]] = []
        for slot in tool_buffers.values():
            args_raw = slot.get("input_json") or ""
            parsed: Any = {}
            if args_raw:
                try:
                    parsed = _json.loads(args_raw)
                except _json.JSONDecodeError:
                    parsed = {}
            tool_uses.append({
                "id": slot.get("id") or "",
                "name": slot.get("name") or "",
                "input": parsed if isinstance(parsed, dict) else {},
            })

        text = "".join(text_parts).strip() or "(empty reply)"
        thinking = "\n".join(p for p in thinking_parts if p).strip() or None
        # Single trailing usage.updated carrying everything the
        # consumer needs to rebuild the final dict.
        yield LLMStreamEvent("usage.updated", {
            "model": model_name,
            "stop_reason": stop_reason,
            "usage": usage_dict or {},
            "tool_uses": tool_uses,
            "text": text,
            "thinking": thinking,
            "raw_blocks": raw_blocks,
        })

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


def _dump(obj: Any) -> dict[str, Any] | None:
    """Best-effort Pydantic → dict; tolerate older SDKs that lack model_dump."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return None


def _response_to_dict(response: Any, default_model: str) -> dict[str, Any]:
    """Translate a non-streaming SDK response into the canonical dict.

    Returns ``{text, thinking, tool_uses, raw_blocks, model, usage,
    stop_reason}``. ``text`` is never empty: if the model produced
    only thinking blocks, it falls back to ``"(empty reply)"``.
    """
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    raw_blocks: list[dict[str, Any]] = []
    tool_uses: list[dict[str, Any]] = []
    for block in getattr(response, "content", []) or []:
        dumped = _dump(block) or {"type": getattr(block, "type", "unknown")}
        raw_blocks.append(dumped)
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif btype == "thinking":
            thinking_parts.append(getattr(block, "thinking", "") or "")
        elif btype == "tool_use":
            tool_uses.append({
                "id": getattr(block, "id", "") or "",
                "name": getattr(block, "name", "") or "",
                "input": dict(getattr(block, "input", {}) or {}),
            })

    usage = _dump(getattr(response, "usage", None))
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
        dumped = _dump(block) or {"type": getattr(block, "type", "unknown")}
        blocks.append(dumped)
    return blocks
