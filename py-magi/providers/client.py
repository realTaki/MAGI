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
    LLMMessage,
    LLMMessageRole,
    LLMThinkingBlock,
    LLMTool,
    LLMToolCall,
)

litellm.telemetry = False
litellm.suppress_debug_info = True
litellm.drop_params = True
litellm.modify_params = True


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
    """Provider settings plus MAGI Job ↔ LiteLLM chat completion."""

    _ok_finish = {"", "stop", "end_turn", "tool_calls", "function_call", "tool_use", "length", "max_tokens"}

    def __init__(
        self,
        *,
        provider_name: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.host: Host | None = None
        self.api_key: str | None = None
        self.model: str | None = None
        self.configure(provider_name=provider_name, api_key=api_key, model=model)

    def configure(
        self,
        *,
        provider_name: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        if provider_name is not None:
            name = provider_name.strip().lower()
            self.host = _BY_ID.get(_ALIASES.get(name, name))
        if api_key is not None:
            self.api_key = api_key
        if model is not None:
            self.model = model

    def context_window(self) -> int | None:
        """Return LiteLLM's input-context limit for the configured route."""
        host = self.host
        if host is None:
            return None
        info = litellm.get_model_info(model=f"{host.prefix}/{self.model or host.default_model}")
        return info.get("max_input_tokens")

    async def complete(self, job: CallLLMJob) -> CallLLMResult:
        host = self.host
        if host is None:
            return self._failed(job, "no LLM provider configured; set provider.name in settings")
        if not self.api_key:
            return self._failed(job, "no API key configured; set provider.api_key in settings")
        params: dict[str, Any] = {
            "model": f"{host.prefix}/{self.model or host.default_model}",
            "messages": [self._request_message(message) for message in job.messages],
            "reasoning_effort": "high",
            "api_key": self.api_key,
            "timeout": 120.0,
            "drop_params": True,
        }
        if host.api_base:
            params["api_base"] = host.api_base
        if job.tools:
            params["tools"] = [self._request_tool(tool) for tool in job.tools]
        try:
            response = await litellm.acompletion(**params)
            choices = getattr(response, "choices", None) or ()
            if not choices:
                return self._failed(job, f"{host.id} provider: response carried no choices")
            choice = choices[0]
            reason = str(getattr(choice, "finish_reason", "") or "").strip().lower()
            if reason not in self._ok_finish:
                return self._failed(job, f"provider finish: {reason or 'unknown'}")
            message = self._assistant_message(choice.message)
            if isinstance(message, str):
                return self._failed(job, message)
            return CallLLMResult(id=job.id, message=message)
        except Exception as exc:  # noqa: BLE001 -- SDK failure belongs on CallLLMResult
            return self._failed(job, str(exc))

    def _failed(self, job: CallLLMJob, error: str) -> CallLLMResult:
        return CallLLMResult(id=job.id, status=JobStatus.FAILED, error=error)

    def _request_tool(self, tool: LLMTool) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    def _request_message(self, message: LLMMessage) -> dict[str, Any]:
        role = LLMMessageRole(message.role)
        if role in {LLMMessageRole.SYSTEM, LLMMessageRole.USER}:
            return {"role": role.value, "content": message.content}
        if role is LLMMessageRole.TOOL:
            content = message.content if not message.is_error else f"Tool failed:\n{message.content}"
            return {"role": "tool", "tool_call_id": message.tool_call_id, "content": content}
        payload: dict[str, Any] = {"role": "assistant", "content": message.content}
        if message.thinking_blocks:
            payload["thinking_blocks"] = [
                {
                    "type": block.type,
                    "thinking": block.thinking,
                    "signature": block.signature,
                }
                for block in message.thinking_blocks
            ]
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        return payload

    def _assistant_message(self, raw: Any) -> LLMMessage | str:
        data = self._mapping(raw)
        if data is None:
            return "provider returned a message that cannot be decoded"
        content = data.get("content")
        if content is None:
            text = ""
        elif isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
                and part.get("type") not in {"thinking", "redacted_thinking"}
            )
        else:
            return "provider returned non-text assistant content"
        blocks = data.get("thinking_blocks")
        thinking_blocks: list[LLMThinkingBlock] = []
        if blocks is not None:
            if not isinstance(blocks, list):
                return "provider returned invalid thinking blocks"
            for item in blocks:
                block = self._mapping(item)
                if block is None or not all(
                    isinstance(block.get(key), str) for key in ("type", "thinking", "signature")
                ):
                    return "provider returned invalid thinking block"
                thinking_blocks.append(
                    LLMThinkingBlock(
                        type=block["type"],
                        thinking=block["thinking"],
                        signature=block["signature"],
                    )
                )
        calls: list[LLMToolCall] = []
        for item in data.get("tool_calls") or ():
            call = self._mapping(item) or {}
            function = self._mapping(call.get("function")) or {}
            if not call.get("id") or not function.get("name"):
                return "provider returned an invalid tool call"
            arguments = self._arguments(function.get("arguments"))
            if isinstance(arguments, str):
                return arguments
            calls.append(
                LLMToolCall(
                    tool_call_id=str(call["id"]),
                    name=str(function["name"]),
                    arguments=arguments,
                )
            )
        return LLMMessage(
            role=LLMMessageRole.ASSISTANT,
            content=text,
            tool_calls=calls or None,
            thinking_blocks=thinking_blocks or None,
        )

    def _arguments(self, value: Any) -> dict[str, Any] | str:
        if value is None or value == "":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        return "provider returned non-object tool arguments"

    def _mapping(self, obj: Any) -> dict[str, Any] | None:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj
        dump = getattr(obj, "model_dump", None)
        if callable(dump):
            dumped = dump()
            if isinstance(dumped, dict):
                return dumped
        data = getattr(obj, "__dict__", None)
        return dict(data) if isinstance(data, dict) else None
