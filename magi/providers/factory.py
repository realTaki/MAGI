"""LLM provider factory — 从 ``bus.settings_book`` 读凭据并构造 provider。

抽象接口 (:class:`LLMProvider` / :class:`LLMStreamEvent` /
:class:`StreamEventKind`) 在 :mod:`magi.providers.base`。本模块只
负责"凭据 → SDK client"这一步，**唯一**知道这件事的地方。

凭据来源是 ``bus.settings_book``（key 见
:mod:`magi.bus.guild.changeProviderConfigJob`）。WebUI / API
channel 通过 ``changeProviderConfigJobBoard.publish()``（self-contained
write，会自动落 settings_book）或直接 ``settings_book.set()`` 写入。

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
from typing import TYPE_CHECKING

from magi.bus.guild.changeProviderConfigJob import (
    PROVIDER_API_KEY_KEY,
    PROVIDER_MODEL_KEY,
    PROVIDER_NAME_KEY,
)
from magi.providers.base import LLMProvider
from magi.providers.claude_code import ClaudeProvider
from magi.providers.errors import LLMError, LLMNotConfiguredError
from magi.providers.minimax import MinimaxProvider
from magi.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.providers.factory")

# ── known provider ids (module-private; for error messages) ───────────────

_KNOWN_PROVIDERS: list[str] = [
    "claude",
    "minimax-global",
    "minimax-cn",
    "openai",
]


# ── factory: 从 settings_book 读凭据并实例化 provider ──────────────────────


def get_provider(*, bus: Bus, model: str | None = None) -> LLMProvider:
    """从 ``bus.settings_book`` 读凭据并实例化 provider。

    Parameters
    ----------
    bus
        组合根注入的 :class:`Bus`。凭据来源唯一是
        ``settings_book``（key 形如 ``provider.name`` /
        ``provider.api_key`` / ``provider.model``）。
    model
        可选覆盖。``None`` 表示用配置里的默认模型。

    Raises
    ------
    LLMNotConfiguredError
        provider 或 api_key 未设置。
    LLMError
        provider 不在已知列表里。
    """
    provider_name = bus.settings_book.get(key=PROVIDER_NAME_KEY)
    api_key = bus.settings_book.get(key=PROVIDER_API_KEY_KEY)
    effective_model = model or bus.settings_book.get(key=PROVIDER_MODEL_KEY)

    if not provider_name:
        raise LLMNotConfiguredError("no LLM provider configured; set provider.name in settings")
    if not api_key:
        raise LLMNotConfiguredError("no API key configured; set provider.api_key in settings")
    return _build_provider(
        provider_name=provider_name,
        api_key=api_key,
        model=effective_model,
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
