"""LLM provider factory — 用已解析配置构造 SDK client。

抽象接口 (:class:`LLMProvider` / :class:`LLMStreamEvent` /
:class:`StreamEventKind`) 在 :mod:`magi.providers.base`。本模块只
负责"配置 → SDK client"这一步，**唯一**知道具体厂商构造方式的地方。
provider worker 通过 ``magi.bus.BusForWorker`` 发布 ``GetSettingJob``
读取配置，再把原始值传入这里；factory 不依赖任何 BUS 实现或 Book。

已知厂商（v0）:

- ``claude``         — Anthropic 自家 API（first-party）
- ``minimax-cn``     — Minimax 国内节点（Anthropic 兼容）
- ``minimax-global`` — Minimax 海外节点（Anthropic 兼容）
- ``openai``         — OpenAI 官方 chat-completions

``minimax``（不带 region 后缀）是 ``minimax-cn`` 的历史别名。

添加新厂商
==========

1. 在 :mod:`magi.providers` 下新增一个 Python 文件，继承
   :class:`~magi.providers.base.LLMProvider`（或 :class:`LLMProvider`，
   若 wire 协议不是 Anthropic 兼容）。
2. 在本文件 :func:`_build_provider` 加分支。
3. ``_KNOWN_PROVIDERS`` 列表里加 id（私有，供工厂内部错误消息用）。
"""

from __future__ import annotations

import logging
from magi.providers.base import LLMProvider
from magi.providers.claude_code import ClaudeProvider
from magi.providers.errors import LLMError, LLMNotConfiguredError
from magi.providers.minimax import MinimaxProvider
from magi.providers.openai import OpenAIProvider

logger = logging.getLogger("magi.providers.factory")

# ── known provider ids (module-private; for error messages) ───────────────

_KNOWN_PROVIDERS: list[str] = [
    "claude",
    "minimax-global",
    "minimax-cn",
    "openai",
]


# ── factory: 从已解析配置实例化 provider ───────────────────────────────────


def get_provider(
    *,
    provider_name: str | None,
    api_key: str | None,
    model: str | None = None,
) -> LLMProvider:
    """从已解析的 provider 配置构造 SDK client。

    Parameters
    ----------
    provider_name / api_key
        由 provider worker 通过 vNext 的 ``GetSettingJob`` 读取的
        ``provider.name`` / ``provider.api_key`` 配置。
    model
        可选覆盖。``None`` 表示用配置里的默认模型。

    Raises
    ------
    LLMNotConfiguredError
        provider 或 api_key 未设置。
    LLMError
        provider 不在已知列表里。
    """
    if not provider_name:
        raise LLMNotConfiguredError("no LLM provider configured; set provider.name in settings")
    if not api_key:
        raise LLMNotConfiguredError("no API key configured; set provider.api_key in settings")
    return _build_provider(
        provider_name=provider_name,
        api_key=api_key,
        model=model,
    )


def _build_provider(
    *,
    provider_name: str,
    api_key: str,
    model: str | None = None,
) -> LLMProvider:
    """Construct the concrete provider from raw credentials."""
    name = provider_name.strip().lower()
    if name in ("minimax", "minimax-cn"):
        return MinimaxProvider.for_region(
            "minimax-cn",
            api_key=api_key,
            model=model,
        )
    if name == "minimax-global":
        return MinimaxProvider.for_region(
            "minimax-global",
            api_key=api_key,
            model=model,
        )
    if name == "claude":
        return ClaudeProvider(api_key=api_key, model=model)
    if name == "openai":
        return OpenAIProvider(api_key=api_key, model=model)

    raise LLMError(f"Unknown LLM provider: {provider_name!r}. Known: {', '.join(_KNOWN_PROVIDERS)}")


__all__ = ["get_provider"]
