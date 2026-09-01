"""LiteLLM adapter for MAGI's backend-neutral LLM Job contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import litellm

from bus import (
    CallLLMJob,
    CallLLMResult,
    JobStatus,
    LLMFinishReason,
    LLMMessage,
    LLMMessageRole,
    LLMTool,
    LLMToolCall,
    LLMUsage,
)

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
    """One MAGI picker entry backed by a LiteLLM route."""

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
    Host("minimax-cn", "minimax", ("MiniMax-M3", "MiniMax-M2.5"), "MiniMax-M3", "https://api.minimaxi.com/anthropic"),
    Host("minimax-global", "minimax", ("MiniMax-M3", "MiniMax-M2.5"), "MiniMax-M3", "https://api.minimax.io/anthropic"),
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


class LiteLLMClient:
    """Mutable provider configuration plus one MAGI-to-LiteLLM adapter."""

    def __init__(
        self,
        *,
        provider_name: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.provider_name: str | None = None
        self.api_key: str | None = None
        self.model: str | None = None
        self.host: Host | None = None
        self.host_error: str | None = "no LLM provider configured; set provider.name in settings"
        self.litellm = litellm
        self.litellm.telemetry = False
        self.litellm.suppress_debug_info = True
        self.litellm.drop_params = True
        self.litellm.modify_params = True
        self.configure(provider_name=provider_name, api_key=api_key, model=model)

    def configure(
        self,
        *,
        provider_name: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        if provider_name is not None:
            self.provider_name = provider_name
            name = provider_name.strip().lower()
            self.host = _BY_ID.get(_ALIASES.get(name, name))
            self.host_error = (
                None
                if self.host is not None
                else f"Unknown LLM provider: {provider_name!r}. Known: {', '.join(item.id for item in HOSTS)}"
            )
        if api_key is not None:
            self.api_key = api_key
        if model is not None:
            self.model = model

    async def complete(self, job: CallLLMJob) -> CallLLMResult:
        if self.host is None:
            return CallLLMResult(id=job.id, status=JobStatus.FAILED, error=self.host_error or "no LLM provider configured; set provider.name in settings")
        if not self.api_key:
            return CallLLMResult(id=job.id, status=JobStatus.FAILED, error="no API key configured; set provider.api_key in settings")
        model = self.model or self.host.default_model
        params: dict[str, Any] = {
            "model": f"{self.host.prefix}/{model}",
            "messages": [message.to_dict() for message in job.messages],
            "max_tokens": job.max_output_tokens,
            "api_key": self.api_key,
            "timeout": 30.0,
            "drop_params": True,
        }
        if self.host.api_base:
            params["api_base"] = self.host.api_base
        if job.tools:
            params["tools"] = _tools(job.tools)
        try:
            response = await self.litellm.acompletion(**params)
            choices = getattr(response, "choices", None) or ()
            if not choices:
                return CallLLMResult(id=job.id, status=JobStatus.FAILED, error=f"{self.host.id} provider: response carried no choices")
            return _result(job.id, choices[0].message, response, model)
        except Exception as exc:  # noqa: BLE001 -- every adapter/SDK failure becomes a Job failure
            return CallLLMResult(id=job.id, status=JobStatus.FAILED, error=_error_text(exc, self.host.id))

def _tools(tools: list[LLMTool]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def _result(job_id: int | None, raw_message: Any, raw_response: Any, fallback_model: str) -> CallLLMResult:
    message = _response_message(raw_message)
    model = str(getattr(raw_response, "model", "") or "") or fallback_model
    if "/" in model:
        model = model.split("/", 1)[1]
    choices = getattr(raw_response, "choices", None) or ()
    reason = getattr(choices[0], "finish_reason", None) if choices else None
    return CallLLMResult(
        id=job_id,
        message=message,
        finish_reason=_finish_reason(reason),
        usage=_usage(getattr(raw_response, "usage", None)),
        model=model,
    )


def _response_message(raw: Any) -> LLMMessage:
    data = _dump(raw)
    if data is None:
        raise ValueError("provider returned a message that cannot be decoded")
    content = data.get("content")
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        )
    else:
        raise ValueError("provider returned non-text assistant content")
    calls: list[LLMToolCall] = []
    for raw_call in data.get("tool_calls") or ():
        call = _dump(raw_call)
        function = _dump(call.get("function")) if call else None
        if not call or not function or not call.get("id") or not function.get("name"):
            raise ValueError("provider returned an invalid tool call")
        calls.append(
            LLMToolCall(
                tool_call_id=str(call["id"]),
                name=str(function["name"]),
                arguments=_arguments(function.get("arguments")),
            )
        )
    return LLMMessage(role=LLMMessageRole.ASSISTANT, text=text, tool_calls=calls)


def _arguments(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("provider returned non-object tool arguments")


def _usage(raw_usage: Any) -> LLMUsage | None:
    raw = _dump(raw_usage)
    if raw is None:
        return None
    input_tokens = _integer(raw.get("prompt_tokens") or raw.get("input_tokens"))
    output_tokens = _integer(raw.get("completion_tokens") or raw.get("output_tokens"))
    details = _dump(raw.get("prompt_tokens_details")) or {}
    cached_input_tokens = _integer(raw.get("cache_read_input_tokens") or details.get("cached_tokens"))
    if input_tokens == output_tokens == cached_input_tokens == 0:
        return None
    return LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def _finish_reason(value: Any) -> LLMFinishReason | None:
    reason = str(value or "").strip().lower()
    if reason in {"stop", "end_turn"}:
        return LLMFinishReason.END_TURN
    if reason in {"tool_calls", "function_call", "tool_use"}:
        return LLMFinishReason.TOOL_USE
    if reason in {"length", "max_tokens"}:
        return LLMFinishReason.MAX_OUTPUT
    if reason in {"content_filter", "safety", "refusal"}:
        return LLMFinishReason.REFUSED
    return None


def _error_text(exc: BaseException, label: str) -> str:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if any(marker in text for marker in _CONTEXT_MARKERS):
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
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            dumped = fn()
            if isinstance(dumped, dict):
                return dumped
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return None


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
