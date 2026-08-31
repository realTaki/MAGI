"""LiteLLM client and MAGI's operator menu.

HOSTS is a curated slice of LiteLLM's catalog: common first-party
providers and their current flagship chat models. First-party API
roots stay inside LiteLLM. MiniMax is split into CN / Global because
LiteLLM treats them as one provider with two URLs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("providers.client")

_LITELLM_READY = False
_CONTEXT_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "prompt is too long",
    "reduce the length",
    "tokens must be reduced",
)


@dataclass(frozen=True)
class Host:
    """One MAGI picker entry.

    ``prefix`` is LiteLLM's route. ``api_base`` is only for regional
    hosts. ``models`` are current LiteLLM catalog ids, without the prefix.
    """

    id: str
    prefix: str
    models: tuple[str, ...]
    default_model: str
    api_base: str | None = None


HOSTS: tuple[Host, ...] = (
    Host("claude", "anthropic", ("claude-opus-5", "claude-fable-5", "claude-sonnet-5"), "claude-opus-5"),
    Host("openai", "openai", ("gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"), "gpt-5.6"),
    Host("gemini", "gemini", ("gemini-3.7-flash", "gemini-3.5-flash", "gemini-pro-latest"), "gemini-3.7-flash"),
    Host("xai", "xai", ("grok-4.6", "grok-4.20"), "grok-4.6"),
    Host("deepseek", "deepseek", ("deepseek-v4-pro", "deepseek-v4-flash"), "deepseek-v4-pro"),
    Host("mistral", "mistral", ("mistral-large-latest", "mistral-medium-latest"), "mistral-large-latest"),
    Host(
        "minimax-cn",
        "minimax",
        ("MiniMax-M3", "MiniMax-M2.5"),
        "MiniMax-M3",
        "https://api.minimaxi.com/anthropic",
    ),
    Host(
        "minimax-global",
        "minimax",
        ("MiniMax-M3", "MiniMax-M2.5"),
        "MiniMax-M3",
        "https://api.minimax.io/anthropic",
    ),
)

_ALIASES = {
    "minimax": "minimax-cn",
    "anthropic": "claude",
    "google": "gemini",
    "grok": "xai",
}
_BY_ID = {host.id: host for host in HOSTS}


def options() -> list[dict[str, str]]:
    return [{"provider": host.id, "model": model} for host in HOSTS for model in host.models]


def connect(
    provider_name: str | None,
    api_key: str | None,
    model: str | None = None,
    *,
    client: Client | None = None,
) -> Client:
    if not provider_name:
        raise RuntimeError("no LLM provider configured; set provider.name in settings")
    if not api_key:
        raise RuntimeError("no API key configured; set provider.api_key in settings")
    name = provider_name.strip().lower()
    host = _BY_ID.get(_ALIASES.get(name, name))
    if host is None:
        known = ", ".join(item.id for item in HOSTS)
        raise RuntimeError(f"Unknown LLM provider: {provider_name!r}. Known: {known}")
    if client is None:
        return Client(host=host, api_key=api_key, model=model or host.default_model)
    client.host = host
    client.api_key = api_key
    client.model = model or client.model
    return client


class Client:
    """One cached LiteLLM route."""

    def __init__(self, *, host: Host, api_key: str, model: str) -> None:
        self.host = host
        self.api_key = api_key
        self.model = model

    async def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        tools: list[dict] | None,
    ) -> dict[str, Any]:
        litellm = _litellm()
        params: dict[str, Any] = {
            "model": f"{self.host.prefix}/{self.model}",
            "messages": _messages(messages),
            "max_tokens": max_tokens,
            "api_key": self.api_key,
            "timeout": 30.0,
            "drop_params": True,
        }
        if self.host.api_base:
            params["api_base"] = self.host.api_base
        converted = _tools(tools)
        if converted:
            params["tools"] = converted
        try:
            response = await litellm.acompletion(**params)
        except Exception as exc:  # noqa: BLE001 -- every SDK failure becomes a readable error
            raise RuntimeError(_error_text(exc, self.host.id)) from exc
        choices = getattr(response, "choices", None) or ()
        if not choices:
            raise RuntimeError(f"{self.host.id} provider: response carried no choices")
        return _result(choices[0].message, response, self.model)


def _litellm():
    global _LITELLM_READY
    import litellm

    if not _LITELLM_READY:
        litellm.telemetry = False
        litellm.suppress_debug_info = True
        litellm.drop_params = True
        litellm.modify_params = True
        _LITELLM_READY = True
    return litellm


def _tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    out: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise RuntimeError(f"provider received non-dict tool: {tool!r}")
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            out.append(tool)
            continue
        name = tool.get("name")
        if not name:
            raise RuntimeError("provider received a tool without a name")
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
        )
    return out


def _messages(messages: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    system_taken = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "system" and not system_taken:
            system_taken = True
            out.append({"role": "system", "content": str(message.get("content") or "")})
            continue
        if role == "assistant":
            out.append(_assistant(message))
            continue
        blocks = message.get("content_blocks")
        if not blocks:
            out.append({"role": role or "user", "content": message.get("content") or ""})
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                out.append({"role": "user", "content": json.dumps(block, ensure_ascii=False)})
                continue
            content = block.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id") or block.get("id") or ""),
                    "content": content,
                }
            )
        if message.get("content"):
            out.append({"role": "user", "content": message.get("content")})
    return out


def _assistant(message: dict[str, Any]) -> dict[str, Any]:
    blocks = message.get("content_blocks")
    if not blocks:
        return {"role": "assistant", "content": message.get("content") or ""}
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    thinking_blocks: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and block.get("text"):
            text_parts.append(str(block["text"]))
        elif btype == "thinking":
            thinking_blocks.append(block)
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
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else "",
    }
    if tool_calls:
        assistant["tool_calls"] = tool_calls
    if thinking_blocks:
        assistant["thinking_blocks"] = thinking_blocks
    return assistant


def _result(message: Any, raw: Any, fallback_model: str) -> dict[str, Any]:
    text = _text(message)
    thinking = _thinking(message)
    tool_uses: list[dict[str, Any]] = []
    raw_blocks: list[dict[str, Any]] = []
    for block in getattr(message, "thinking_blocks", None) or ():
        dumped = block if isinstance(block, dict) else _dump(block)
        if dumped:
            raw_blocks.append(dumped)
    for call in getattr(message, "tool_calls", None) or ():
        call_id = str(getattr(call, "id", "") or "")
        fn = getattr(call, "function", None)
        name = str(getattr(fn, "name", "") or "") if fn is not None else ""
        parsed = _args(getattr(fn, "arguments", None) if fn is not None else None)
        tool_uses.append({"id": call_id, "name": name, "input": parsed})
        raw_blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": parsed})
    if text:
        raw_blocks.insert(0, {"type": "text", "text": text})
    if thinking and not any(isinstance(b, dict) and b.get("type") == "thinking" for b in raw_blocks):
        raw_blocks.append({"type": "thinking", "thinking": thinking})
    model = str(getattr(raw, "model", "") or "") or fallback_model
    if "/" in model:
        model = model.split("/", 1)[1]
    finish = None
    choices = getattr(raw, "choices", None)
    if choices:
        finish = getattr(choices[0], "finish_reason", None)
    return {
        "text": text or "(empty reply)",
        "thinking": thinking,
        "tool_uses": tool_uses,
        "raw_blocks": raw_blocks,
        "model": model,
        "usage": _usage(getattr(raw, "usage", None)),
        "stop_reason": _stop(finish),
    }


def _text(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        piece = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
        if isinstance(piece, str):
            parts.append(piece)
    return "".join(parts)


def _thinking(message: Any) -> str | None:
    reasoning = getattr(message, "reasoning_content", None)
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()
    parts: list[str] = []
    for block in getattr(message, "thinking_blocks", None) or ():
        piece = (block.get("thinking") or block.get("text")) if isinstance(block, dict) else (
            getattr(block, "thinking", None) or getattr(block, "text", None)
        )
        if piece:
            parts.append(str(piece))
    for detail in getattr(message, "reasoning_details", None) or ():
        piece = detail.get("text") if isinstance(detail, dict) else getattr(detail, "text", None)
        if piece:
            parts.append(str(piece))
    return "\n".join(parts).strip() or None


def _args(arguments: Any) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {"value": decoded}
    return {"value": arguments}


def _usage(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    raw = _dump(usage) or {}
    details = raw.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else (_dump(details) or {})
    out_details = raw.get("completion_tokens_details")
    out_details = out_details if isinstance(out_details, dict) else (_dump(out_details) or {})
    inp = _int(raw.get("prompt_tokens") or raw.get("input_tokens"))
    out = _int(raw.get("completion_tokens") or raw.get("output_tokens"))
    hit = _int(raw.get("cache_read_input_tokens") or details.get("cached_tokens"))
    write = _int(
        raw.get("cache_creation_input_tokens")
        or details.get("cache_write_tokens")
        or details.get("cache_creation_tokens")
    )
    thinking = _int(out_details.get("reasoning_tokens") or raw.get("thinking_tokens"))
    if inp == 0 and out == 0 and hit == 0 and write == 0:
        return None
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_hit_tokens": hit,
        "cache_write_tokens": write,
        "cache_miss_tokens": max(inp - hit, 0),
        "thinking_tokens": thinking,
        "response_tokens": max(out - thinking, 0),
        "total_tokens": _int(raw.get("total_tokens")) or (inp + out),
    }


def _stop(reason: Any) -> str | None:
    if reason is None:
        return None
    value = str(reason).strip().lower()
    if value in {"stop", "end_turn", "content_filter", "safety"}:
        return "end_turn"
    if value in {"tool_calls", "function_call", "tool_use"}:
        return "tool_use"
    if value in {"length", "max_tokens"}:
        return "max_tokens"
    return value or None


def _error_text(exc: BaseException, label: str) -> str:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if any(marker in str(exc).lower() for marker in _CONTEXT_MARKERS):
        return f"{label} context overflow: {exc}"
    if "auth" in name or "permission" in name:
        return f"{label} auth/permission failed: {exc}"
    if "ratelimit" in name or "rate_limit" in name or "rate limit" in text:
        return f"{label} rate limited: {exc}"
    if "timeout" in name or "connection" in name:
        return f"{label} network error: {exc}"
    if "badrequest" in name or "invalidrequest" in name:
        return f"{label} bad request: {exc}"
    if status:
        return f"{label} status {status}: {exc}"
    return f"{label} error: {exc}"


def _dump(obj: Any) -> dict[str, Any] | None:
    if obj is None:
        return None
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    if hasattr(obj, "__dict__"):
        try:
            return dict(obj.__dict__)
        except Exception:
            return None
    return None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
