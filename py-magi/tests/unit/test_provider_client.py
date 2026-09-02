from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from bus import (
    CallLLMJob,
    ChangeProviderNotify,
    JobStatus,
    LLMFinishReason,
    LLMMessage,
    LLMMessageRole,
    LLMThinkingBlock,
    LLMTool,
    LLMToolCall,
    LLMUsage,
)
from providers.client import LiteLLMClient


def test_client_applies_provider_configuration_in_place() -> None:
    client = LiteLLMClient(provider_name="openai", api_key="old-key", model="gpt-5.6")

    client.configure(provider_name="claude", api_key="new-key", model="claude-sonnet-5")
    client.configure(model="claude-opus-5")

    assert (client.provider_name, client.api_key, client.model) == ("claude", "new-key", "claude-opus-5")


def test_provider_notify_uses_none_for_an_unchanged_setting() -> None:
    change = ChangeProviderNotify(publisher="test", model="claude-opus-5")

    assert change.provider is None
    assert change.api_key is None
    assert change.model == "claude-opus-5"


def test_client_reads_the_configured_model_context_window(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def get_model_info(**kwargs: Any) -> dict[str, int]:
        seen.update(kwargs)
        return {"max_input_tokens": 128_000}

    monkeypatch.setattr("providers.client.litellm.get_model_info", get_model_info)

    client = LiteLLMClient(provider_name="openai", api_key="key", model="gpt-5.6")

    assert client.context_window() == 128_000
    assert seen["model"] == "openai/gpt-5.6"


def test_llm_dtos_round_trip_through_json_record_data() -> None:
    job = CallLLMJob(
        publisher="test",
        messages=[
            LLMMessage(role=LLMMessageRole.SYSTEM, content="be concise"),
            LLMMessage(
                role=LLMMessageRole.ASSISTANT,
                content="",
                thinking_blocks=[
                    LLMThinkingBlock(
                        type="thinking",
                        thinking="I should call the weather tool.",
                        signature="signature",
                    )
                ],
                tool_calls=[
                    LLMToolCall(
                        tool_call_id="call-1",
                        name="weather",
                        arguments={"city": "Beijing"},
                    )
                ],
            ),
        ],
        tools=[LLMTool(name="weather", description="Get weather", input_schema={"type": "object"})],
    )

    restored = CallLLMJob.parse(job.to_dict())

    assert restored == job
    assert isinstance(restored.messages[1].tool_calls[0], LLMToolCall)
    assert isinstance(restored.messages[1].thinking_blocks[0], LLMThinkingBlock)
    assert isinstance(restored.tools[0], LLMTool)
    assert job.to_dict()["tools"] == [
        {"name": "weather", "description": "Get weather", "input_schema": {"type": "object"}}
    ]


@dataclass
class _FakeMessage:
    data: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        return self.data


@dataclass
class _FakeChoice:
    message: _FakeMessage
    finish_reason: str = "tool_calls"


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice]
    model: str = "openai/gpt-5.6"
    usage: Any = None


class _FakeLiteLLM:
    def __init__(self) -> None:
        self.params: dict[str, Any] | None = None

    async def acompletion(self, **params: Any) -> _FakeResponse:
        self.params = params
        return _FakeResponse(
            choices=[
                _FakeChoice(
                    _FakeMessage(
                        {
                            "role": "assistant",
                            "content": "Calling weather",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "weather",
                                        "arguments": '{"city":"Beijing"}',
                                    },
                                }
                            ],
                        }
                    )
                )
            ],
            usage=_FakeMessage({"prompt_tokens": 10, "completion_tokens": 4}),
        )


@pytest.mark.asyncio
async def test_client_maps_only_the_public_llm_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLiteLLM()
    monkeypatch.setattr("providers.client.litellm", fake)
    client = LiteLLMClient(provider_name="openai", api_key="key", model="gpt-5.6")
    job = CallLLMJob(
        id=7,
        publisher="test",
        messages=[
            LLMMessage(role=LLMMessageRole.USER, content="What is the weather?"),
            LLMMessage(role=LLMMessageRole.TOOL, tool_call_id="earlier", content="sunny"),
        ],
        tools=[LLMTool(name="weather", description="Get weather", input_schema={"type": "object"})],
    )

    result = await client.complete(job)

    assert fake.params == {
        "model": "openai/gpt-5.6",
        "messages": [
            {"role": "user", "content": "What is the weather?"},
            {"role": "tool", "tool_call_id": "earlier", "content": "sunny"},
        ],
        "reasoning_effort": "high",
        "api_key": "key",
        "timeout": 120.0,
        "drop_params": True,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "Get weather",
                    "parameters": {"type": "object"},
                },
            }
        ],
    }
    assert result.status is JobStatus.COMPLETED
    assert result.message == LLMMessage(
        role=LLMMessageRole.ASSISTANT,
        content="Calling weather",
        tool_calls=[
            LLMToolCall(
                tool_call_id="call-1",
                name="weather",
                arguments={"city": "Beijing"},
            )
        ],
    )
    assert result.finish_reason is LLMFinishReason.TOOL_USE
    assert result.usage == LLMUsage(input_tokens=10, output_tokens=4)
    assert result.model == "gpt-5.6"


@pytest.mark.asyncio
async def test_invalid_provider_configuration_fails_the_call_job() -> None:
    result = await LiteLLMClient(provider_name="not-a-provider", api_key="key").complete(
        CallLLMJob(
            id=2,
            publisher="test",
            messages=[LLMMessage(role=LLMMessageRole.USER, content="hi")],
            tools=[],
        )
    )

    assert result.id == 2
    assert result.status is JobStatus.FAILED
    assert result.error is not None
    assert "Unknown LLM provider" in result.error
