"""LLM provider 抽象接口 + 流式事件类型。

抽象层边界
==========

:class:`LLMProvider` 是 providers 包的**唯一**对外契约，运行时
(:class:`~magi.providers.worker.ProvidersWorker`) 只通过它调用
模型，与具体厂商解耦。Wire format 直接是 ``list[dict]``（vNext
:class:`~magi.new_bus.firmware.jobs.callLLMJob.CallLLMJob` 上的
``messages`` 字段），不再使用中间 dataclass；每个 provider 子类
内部自行翻成 SDK 期望的形状。返回值是 plain dict，与
:class:`~magi.new_bus.firmware.jobs.callLLMJob.CallLLMResult` 的结果
字段直接对齐。

设计要点
========

- :meth:`LLMProvider.chat` 返回 ``dict``，键集合固定
  （``text / thinking / tool_uses / raw_blocks / model / usage /
  stop_reason``），worker 拿来直接 pack 成 :class:`CallLLMResult`。
  返回 dict 而非 dataclass 是为了避免在 provider 包内再定义一个
  "wire result" 类型——这层契约由 bus 的 ``CallLLMResult``
  表达。

- :meth:`LLMProvider.stream` 返回 :class:`AsyncIterator` yield
  :class:`LLMStreamEvent`。这是 provider SDK 的可选能力；vNext
  provider worker 的首个版本只调用 ``chat()``，直到 BUS 定义持久化
  的 streaming 协议。provider 包本身不维护 message queue。

- :class:`LLMStreamEvent` 是 **provider ↔ worker 之间**的 typed
  契约，不属于 BUS Firmware，也不会出现在 vNext 的 Job result 中。

- :class:`LLMStreamEvent.kind` 是 ``"text.delta"`` /
  ``"tool_arguments.delta"`` / ``"usage.updated"`` 之一。
  worker 在最后收到 ``"usage.updated"`` 时把它当作终态 payload
  直接 pack 进 ``CallLLMResult``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal

StreamEventKind = Literal["text.delta", "tool_arguments.delta", "usage.updated"]


class LLMStreamEvent:
    """Stream 上 yield 一次的增量事件。

    ``kind`` 决定 ``payload`` 的形状：

    - ``"text.delta"``: ``{"text": str}``
    - ``"tool_arguments.delta"``: ``{"partial_json": str, "id": str, "name": str}``
    - ``"usage.updated"``: ``{"input_tokens": int, "output_tokens": int, ...}``
    """

    __slots__ = ("kind", "payload")

    def __init__(self, kind: StreamEventKind, payload: dict[str, Any]) -> None:
        self.kind = kind
        self.payload = payload

    def __repr__(self) -> str:  # pragma: no cover -- debug aid
        return f"LLMStreamEvent(kind={self.kind!r}, payload={self.payload!r})"


class LLMProvider(ABC):
    """LLM 厂商无关的调用接口。

    子类需要：

    - 设置 :attr:`name`（实例 / 类属性均可）：canonical provider id，
      出现在 token audit row 和 provider 配置里。
    - 实现 :meth:`default_model`：调用方没显式传 model 时用这个。
    - 实现 :meth:`chat`：返回 dict（见模块 docstring）。
    - 可选覆写 :meth:`stream`：默认实现是 chat() 的退化版
      （yield 一次 text.delta + 一次 usage.updated）。原生 SDK
      流式支持的子类（Anthropic / OpenAI）都覆写了它。
    """

    name: str = ""

    def __init__(self, api_key: str, model: str | None = None) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.model = model or self.default_model()

    @abstractmethod
    def default_model(self) -> str:
        """The default model id when the caller didn't specify one."""

    @abstractmethod
    async def chat(
        self,
        system: str | None,
        messages: list[dict],
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        """One chat turn. Return ``{text, thinking, tool_uses,
        raw_blocks, model, usage, stop_reason}`` (all optional except
        ``text`` and ``tool_uses``).
        """

    def stream(
        self,
        system: str | None,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Default stream: call :meth:`chat` and emit the text + usage
        as a single delta each.

        Subclasses with native SDK streaming should override this so
        the consumer (the worker) sees incremental deltas rather than
        one final bundle. The terminal ``usage.updated`` event's
        payload is treated as the authoritative final-state dict by
        the worker — subclasses should pack the same fields
        :meth:`chat` returns.
        """

        async def _iterator() -> AsyncIterator[LLMStreamEvent]:
            result = await self.chat(
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                tools=tools,
            )
            text = result.get("text") or ""
            if text:
                yield LLMStreamEvent("text.delta", {"text": text})
            usage = result.get("usage")
            if usage:
                yield LLMStreamEvent("usage.updated", dict(usage))

        return _iterator()


__all__ = ["LLMProvider", "LLMStreamEvent", "StreamEventKind"]
