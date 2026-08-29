"""Unit tests for :class:`providers.openai.OpenAIProvider`.

The OpenAI SDK is fully mocked — every test patches
``providers.openai.AsyncOpenAI`` so no network call
or real ``AsyncOpenAI`` instantiation ever happens. The
tests focus on:

  - the request payload (system, messages, tool schemas)
    sent to the SDK
  - the response → dict translation
    (text, parallel tool calls, finish reasons, usage,
    reasoning metadata, malformed arguments)
  - the streaming ``AsyncIterator[LLMStreamEvent]``
    (text deltas, tool-arg deltas, parallel tool calls,
    usage.updated terminal event)
  - the typed error mapping (auth, rate-limit, network,
    context-length, generic)
  - the factory wiring (``openai`` accepted as a
    provider id, runtime instantiation via ``Bus``)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from old_bus.firmwares.jobs.changeProviderConfigJob import (
    PROVIDER_API_KEY_KEY,
    PROVIDER_MODEL_KEY,
    PROVIDER_NAME_KEY,
)
from providers.base import LLMStreamEvent
from providers.errors import (
    LLMAuthError,
    LLMContextLengthError,
    LLMError,
    LLMNetworkError,
    LLMRateLimitError,
)
from providers.factory import get_provider
from providers.openai import OpenAIProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(
    *,
    content: str | None = "hello",
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_details: list[Any] | None = None,
) -> MagicMock:
    """Build a mock OpenAI ``ChatCompletionMessage`` for tests."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls or []
    message.reasoning_details = reasoning_details or []
    return message


def _make_tool_call(
    *,
    call_id: str,
    name: str,
    arguments: str,
    index: int = 0,
) -> MagicMock:
    call = MagicMock()
    call.id = call_id
    call.index = index
    fn = MagicMock()
    fn.name = name
    fn.arguments = arguments
    call.function = fn
    return call


def _make_response(
    *,
    message: MagicMock,
    finish_reason: str = "stop",
    model: str = "gpt-4o-mini",
    usage: dict[str, Any] | None | object = None,
) -> MagicMock:
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    response.model = model
    if usage is None:
        usage_obj = MagicMock()
        usage_obj.model_dump = MagicMock(
            return_value={
                "prompt_tokens": 11,
                "completion_tokens": 22,
                "total_tokens": 33,
            }
        )
        response.usage = usage_obj
    elif usage is _NO_USAGE:
        response.usage = None
    else:
        usage_obj = MagicMock()
        usage_obj.model_dump = MagicMock(return_value=dict(usage))
        response.usage = usage_obj
    return response


_NO_USAGE = object()


@pytest.fixture
def mock_openai():
    """Patch ``AsyncOpenAI`` in the provider module and yield the instance.

    The class is patched (not the instance) so
    ``OpenAIProvider.__init__`` gets a fully mocked
    ``AsyncOpenAI`` that doesn't try to validate the API
    key or open a real connection.
    """
    with patch("providers.openai.AsyncOpenAI") as mock_cls:
        instance = mock_cls.return_value
        instance.chat.completions.create = AsyncMock()
        yield instance


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_requires_api_key():
    with pytest.raises(ValueError, match="api_key is required"):
        OpenAIProvider(api_key="")


def test_constructor_uses_default_model_when_unspecified():
    with patch("providers.openai.AsyncOpenAI") as mock_cls:
        provider = OpenAIProvider(api_key="sk-test")
    assert provider.model == provider.default_model()
    assert provider.name == "openai"
    mock_cls.assert_called_once()


def test_default_model_is_a_string():
    with patch("providers.openai.AsyncOpenAI"):
        provider = OpenAIProvider(api_key="sk-test")
    assert isinstance(provider.default_model(), str)
    assert provider.default_model()  # non-empty


# ---------------------------------------------------------------------------
# chat(): request shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_passes_system_and_messages(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content="hi there"),
    )
    provider = OpenAIProvider(api_key="sk-test")
    result = await provider.chat(
        system="you are helpful",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=128,
    )
    assert result["text"] == "hi there"
    call_kwargs = mock_openai.chat.completions.create.await_args.kwargs
    assert call_kwargs["model"] == provider.model
    assert call_kwargs["max_tokens"] == 128
    assert "stream" not in call_kwargs
    sdk_messages = call_kwargs["messages"]
    assert sdk_messages[0] == {"role": "system", "content": "you are helpful"}
    assert sdk_messages[1] == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_chat_omits_system_when_none(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content="ok"),
    )
    provider = OpenAIProvider(api_key="sk-test")
    await provider.chat(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
    )
    sdk_messages = mock_openai.chat.completions.create.await_args.kwargs["messages"]
    assert all(m["role"] != "system" for m in sdk_messages)


@pytest.mark.asyncio
async def test_chat_converts_tools_to_openai_shape(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content="ok"),
    )
    provider = OpenAIProvider(api_key="sk-test")
    await provider.chat(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        tools=[
            {
                "name": "get_weather",
                "description": "weather lookup",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ],
    )
    sdk_tools = mock_openai.chat.completions.create.await_args.kwargs["tools"]
    assert sdk_tools == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "weather lookup",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]


@pytest.mark.asyncio
async def test_chat_passes_through_passthrough_function_tool(mock_openai):
    """A tool already in OpenAI shape is forwarded unchanged."""
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content="ok"),
    )
    provider = OpenAIProvider(api_key="sk-test")
    passthrough = {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "weather lookup",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    await provider.chat(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        tools=[passthrough],
    )
    assert mock_openai.chat.completions.create.await_args.kwargs["tools"] == [passthrough]


# ---------------------------------------------------------------------------
# chat(): message history conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_replays_assistant_tool_use_blocks(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content="ok"),
    )
    provider = OpenAIProvider(api_key="sk-test")
    replay = [
        {"type": "text", "text": "calling the tool"},
        {
            "type": "tool_use",
            "id": "call-1",
            "name": "get_weather",
            "input": {"city": "Beijing"},
        },
    ]
    await provider.chat(
        system=None,
        messages=[
            {"role": "user", "content": "what's the weather?"},
            {"role": "assistant", "content": "calling the tool", "content_blocks": replay},
        ],
        max_tokens=16,
    )
    sdk_messages = mock_openai.chat.completions.create.await_args.kwargs["messages"]
    assert sdk_messages[0] == {"role": "user", "content": "what's the weather?"}
    assistant_msg = sdk_messages[1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == "calling the tool"
    assert assistant_msg["tool_calls"] == [
        {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": json.dumps({"city": "Beijing"}),
            },
        }
    ]


@pytest.mark.asyncio
async def test_chat_emits_tool_role_messages_for_tool_results(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content="ok"),
    )
    provider = OpenAIProvider(api_key="sk-test")
    await provider.chat(
        system=None,
        messages=[
            {
                "role": "user",
                "content": "",
                "content_blocks": [
                    {"type": "tool_result", "tool_use_id": "call-1", "content": "21C"},
                    {"type": "tool_result", "tool_use_id": "call-2", "content": "rainy"},
                ],
            },
        ],
        max_tokens=16,
    )
    sdk_messages = mock_openai.chat.completions.create.await_args.kwargs["messages"]
    assert sdk_messages == [
        {"role": "tool", "tool_call_id": "call-1", "content": "21C"},
        {"role": "tool", "tool_call_id": "call-2", "content": "rainy"},
    ]


@pytest.mark.asyncio
async def test_chat_user_message_with_text_and_tool_results(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content="ok"),
    )
    provider = OpenAIProvider(api_key="sk-test")
    await provider.chat(
        system=None,
        messages=[
            {
                "role": "user",
                "content": "here are the results",
                "content_blocks": [
                    {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}
                ],
            },
        ],
        max_tokens=16,
    )
    sdk_messages = mock_openai.chat.completions.create.await_args.kwargs["messages"]
    assert sdk_messages[0] == {"role": "tool", "tool_call_id": "call-1", "content": "ok"}
    assert sdk_messages[1] == {"role": "user", "content": "here are the results"}


# ---------------------------------------------------------------------------
# chat(): response → dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_parses_parallel_tool_calls(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(
            content="",
            tool_calls=[
                _make_tool_call(
                    call_id="call-A", name="get_weather", arguments='{"city":"A"}', index=0
                ),
                _make_tool_call(
                    call_id="call-B", name="get_time", arguments='{"zone":"B"}', index=1
                ),
            ],
        ),
        finish_reason="tool_calls",
    )
    provider = OpenAIProvider(api_key="sk-test")
    result = await provider.chat(
        system=None,
        messages=[{"role": "user", "content": "go"}],
        max_tokens=16,
    )
    assert len(result["tool_uses"]) == 2
    by_id = {tu["id"]: tu for tu in result["tool_uses"]}
    assert by_id["call-A"]["name"] == "get_weather"
    assert by_id["call-A"]["input"] == {"city": "A"}
    assert by_id["call-B"]["name"] == "get_time"
    assert result["stop_reason"] == "tool_use"
    types = [block["type"] for block in result["raw_blocks"]]
    assert "tool_use" in types
    assert result["text"] == "(empty reply)"


@pytest.mark.asyncio
async def test_chat_normalizes_finish_reasons(mock_openai):
    provider = OpenAIProvider(api_key="sk-test")
    cases = [
        ("stop", "end_turn"),
        ("tool_calls", "tool_use"),
        ("function_call", "tool_use"),
        ("length", "max_tokens"),
        ("content_filter", "end_turn"),
        ("end_turn", "end_turn"),
    ]
    for upstream, expected in cases:
        mock_openai.chat.completions.create.return_value = _make_response(
            message=_make_message(content="x"),
            finish_reason=upstream,
        )
        result = await provider.chat(
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )
        assert result["stop_reason"] == expected, (upstream, result["stop_reason"])


@pytest.mark.asyncio
async def test_chat_normalizes_usage_keys(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content="x"),
        usage={
            "prompt_tokens": 7,
            "completion_tokens": 13,
            "total_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
    )
    provider = OpenAIProvider(api_key="sk-test")
    result = await provider.chat(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=8,
    )
    assert result["usage"] is not None
    assert result["usage"]["input_tokens"] == 7
    assert result["usage"]["output_tokens"] == 13
    assert result["usage"]["total_tokens"] == 20
    # extra details are preserved
    assert result["usage"]["prompt_tokens_details"] == {"cached_tokens": 3}


@pytest.mark.asyncio
async def test_chat_records_optional_reasoning_details(mock_openai):
    class _Detail:
        def __init__(self, text: str) -> None:
            self.text = text

    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(
            content="answer",
            reasoning_details=[_Detail("step 1"), _Detail("step 2")],
        ),
    )
    provider = OpenAIProvider(api_key="sk-test")
    result = await provider.chat(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=8,
    )
    assert result["thinking"] == "step 1\nstep 2"
    # raw_blocks should now contain a thinking block
    assert any(b.get("type") == "thinking" for b in result["raw_blocks"])


@pytest.mark.asyncio
async def test_chat_handles_malformed_tool_arguments(mock_openai):
    """Bad JSON in tool arguments falls back to an empty input dict."""
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(
            content="",
            tool_calls=[
                _make_tool_call(
                    call_id="call-1",
                    name="get_weather",
                    arguments="{not-json",
                ),
            ],
        ),
        finish_reason="tool_calls",
    )
    provider = OpenAIProvider(api_key="sk-test")
    result = await provider.chat(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=8,
    )
    assert result["tool_uses"] == [{"id": "call-1", "name": "get_weather", "input": {}}]


@pytest.mark.asyncio
async def test_chat_returns_empty_reply_when_text_missing(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content=None),
        finish_reason="stop",
    )
    provider = OpenAIProvider(api_key="sk-test")
    result = await provider.chat(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=8,
    )
    assert result["text"] == "(empty reply)"


@pytest.mark.asyncio
async def test_chat_records_model_name_from_response(mock_openai):
    mock_openai.chat.completions.create.return_value = _make_response(
        message=_make_message(content="ok"),
        model="gpt-4o-2024-08-06",
    )
    provider = OpenAIProvider(api_key="sk-test")
    result = await provider.chat(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=8,
    )
    assert result["model"] == "gpt-4o-2024-08-06"


# ---------------------------------------------------------------------------
# chat(): error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_auth_error_maps_to_LLMAuthError(mock_openai):
    mock_openai.chat.completions.create.side_effect = openai.AuthenticationError(
        message="bad key",
        response=MagicMock(status_code=401),
        body=None,
    )
    provider = OpenAIProvider(api_key="sk-test")
    with pytest.raises(LLMAuthError):
        await provider.chat(
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )


@pytest.mark.asyncio
async def test_chat_permission_denied_maps_to_LLMAuthError(mock_openai):
    mock_openai.chat.completions.create.side_effect = openai.PermissionDeniedError(
        message="forbidden",
        response=MagicMock(status_code=403),
        body=None,
    )
    provider = OpenAIProvider(api_key="sk-test")
    with pytest.raises(LLMAuthError):
        await provider.chat(
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )


@pytest.mark.asyncio
async def test_chat_rate_limit_maps_to_LLMRateLimitError(mock_openai):
    mock_openai.chat.completions.create.side_effect = openai.RateLimitError(
        message="429",
        response=MagicMock(status_code=429),
        body=None,
    )
    provider = OpenAIProvider(api_key="sk-test")
    with pytest.raises(LLMRateLimitError):
        await provider.chat(
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )


@pytest.mark.asyncio
async def test_chat_timeout_maps_to_LLMNetworkError(mock_openai):
    mock_openai.chat.completions.create.side_effect = openai.APITimeoutError(request=MagicMock())
    provider = OpenAIProvider(api_key="sk-test")
    with pytest.raises(LLMNetworkError):
        await provider.chat(
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )


@pytest.mark.asyncio
async def test_chat_connection_error_maps_to_LLMNetworkError(mock_openai):
    mock_openai.chat.completions.create.side_effect = openai.APIConnectionError(request=MagicMock())
    provider = OpenAIProvider(api_key="sk-test")
    with pytest.raises(LLMNetworkError):
        await provider.chat(
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )


@pytest.mark.asyncio
async def test_chat_bad_request_context_length_maps(mock_openai):
    mock_openai.chat.completions.create.side_effect = openai.BadRequestError(
        message="This model's maximum context length is 4096 tokens. Please reduce the length of the messages.",
        response=MagicMock(status_code=400),
        body=None,
    )
    provider = OpenAIProvider(api_key="sk-test")
    with pytest.raises(LLMContextLengthError):
        await provider.chat(
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )


@pytest.mark.asyncio
async def test_chat_bad_request_other_maps_to_LLMError(mock_openai):
    mock_openai.chat.completions.create.side_effect = openai.BadRequestError(
        message="invalid request body",
        response=MagicMock(status_code=400),
        body=None,
    )
    provider = OpenAIProvider(api_key="sk-test")
    with pytest.raises(LLMError):
        await provider.chat(
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )


@pytest.mark.asyncio
async def test_chat_status_error_maps_to_LLMNetworkError(mock_openai):
    mock_openai.chat.completions.create.side_effect = openai.APIStatusError(
        message="server error",
        response=MagicMock(status_code=500),
        body=None,
    )
    provider = OpenAIProvider(api_key="sk-test")
    with pytest.raises(LLMNetworkError):
        await provider.chat(
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )


# ---------------------------------------------------------------------------
# stream(): text + parallel tool calls + usage
# ---------------------------------------------------------------------------


def _make_chunk(
    *,
    delta: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    model: str | None = None,
    usage: Any = None,
) -> MagicMock:
    chunk = MagicMock()
    chunk.choices = []
    chunk.model = model
    chunk.usage = usage
    if delta is not None or finish_reason is not None:
        choice = MagicMock()
        choice.finish_reason = finish_reason
        choice.delta = MagicMock()
        choice.delta.content = (delta or {}).get("content")
        choice.delta.tool_calls = (delta or {}).get("tool_calls") or []
        choice.delta.reasoning_details = (delta or {}).get("reasoning_details") or []
        chunk.choices = [choice]
    return chunk


class _AsyncStreamIter:
    """Helper that mimics OpenAI's async-completion stream iterator."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> _AsyncStreamIter:
        return self

    async def __anext__(self) -> Any:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


@pytest.mark.asyncio
async def test_stream_emits_text_and_tool_deltas(mock_openai):
    async def _stream_chunks(**_kwargs):
        return _AsyncStreamIter(
            [
                _make_chunk(delta={"content": "Hello, "}),
                _make_chunk(delta={"content": "world!"}),
                _make_chunk(
                    delta={
                        "tool_calls": [
                            _make_tool_call(
                                call_id="call-1", name="get_weather", arguments='{"city"', index=0
                            ),
                        ]
                    },
                ),
                _make_chunk(
                    delta={
                        "tool_calls": [
                            _make_tool_call(
                                call_id="call-1", name="get_weather", arguments=':"BJ"}', index=0
                            ),
                        ]
                    },
                ),
                _make_chunk(
                    finish_reason="tool_calls",
                    model="gpt-4o-mini",
                    usage=MagicMock(
                        model_dump=MagicMock(
                            return_value={
                                "prompt_tokens": 5,
                                "completion_tokens": 9,
                                "total_tokens": 14,
                            }
                        )
                    ),
                ),
            ]
        )

    mock_openai.chat.completions.create.side_effect = _stream_chunks
    provider = OpenAIProvider(api_key="sk-test")

    events: list[LLMStreamEvent] = []
    async for event in provider.stream(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
    ):
        events.append(event)

    text_deltas = [e for e in events if e.kind == "text.delta"]
    tool_deltas = [e for e in events if e.kind == "tool_arguments.delta"]
    usage_events = [e for e in events if e.kind == "usage.updated"]

    assert [d.payload["text"] for d in text_deltas] == ["Hello, ", "world!"]
    assert len(tool_deltas) == 2
    assert "".join(d.payload["partial_json"] for d in tool_deltas) == '{"city":"BJ"}'
    terminal = usage_events[-1]
    assert terminal.payload["usage"]["input_tokens"] == 5
    assert terminal.payload["usage"]["output_tokens"] == 9

    assert terminal.payload["text"] == "Hello, world!"
    assert terminal.payload["tool_uses"] == [
        {"id": "call-1", "name": "get_weather", "input": {"city": "BJ"}}
    ]
    assert terminal.payload["stop_reason"] == "tool_use"


@pytest.mark.asyncio
async def test_stream_aggregates_parallel_tool_calls(mock_openai):
    async def _stream_chunks(**_kwargs):
        return _AsyncStreamIter(
            [
                _make_chunk(
                    delta={
                        "tool_calls": [
                            _make_tool_call(
                                call_id="call-A",
                                name="get_weather",
                                arguments='{"city":"A"}',
                                index=0,
                            ),
                        ]
                    }
                ),
                _make_chunk(
                    delta={
                        "tool_calls": [
                            _make_tool_call(
                                call_id="call-B", name="get_time", arguments='{"zone":"B"}', index=1
                            ),
                        ]
                    }
                ),
                _make_chunk(finish_reason="tool_calls"),
            ]
        )

    mock_openai.chat.completions.create.side_effect = _stream_chunks
    provider = OpenAIProvider(api_key="sk-test")

    events: list[LLMStreamEvent] = []
    async for event in provider.stream(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
    ):
        events.append(event)

    terminal = next(e for e in events if e.kind == "usage.updated")
    assert len(terminal.payload["tool_uses"]) == 2
    by_id = {tu["id"]: tu for tu in terminal.payload["tool_uses"]}
    assert by_id["call-A"]["input"] == {"city": "A"}
    assert by_id["call-B"]["input"] == {"zone": "B"}


@pytest.mark.asyncio
async def test_stream_text_only_reply(mock_openai):
    async def _stream_chunks(**_kwargs):
        return _AsyncStreamIter(
            [
                _make_chunk(delta={"content": "all"}),
                _make_chunk(delta={"content": " good"}),
                _make_chunk(finish_reason="stop"),
            ]
        )

    mock_openai.chat.completions.create.side_effect = _stream_chunks
    provider = OpenAIProvider(api_key="sk-test")
    events: list[LLMStreamEvent] = []

    async for event in provider.stream(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
    ):
        events.append(event)

    terminal = next(e for e in events if e.kind == "usage.updated")
    assert terminal.payload["text"] == "all good"
    assert terminal.payload["stop_reason"] == "end_turn"
    assert terminal.payload["tool_uses"] == []
    assert [e.payload["text"] for e in events if e.kind == "text.delta"] == ["all", " good"]


@pytest.mark.asyncio
async def test_stream_wraps_openai_error(mock_openai):
    mock_openai.chat.completions.create.side_effect = openai.RateLimitError(
        message="429",
        response=MagicMock(status_code=429),
        body=None,
    )
    provider = OpenAIProvider(api_key="sk-test")

    iterator = provider.stream(
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
    )
    with pytest.raises(LLMRateLimitError):
        async for _ in iterator:
            pass


# ---------------------------------------------------------------------------
# Factory wiring (via Bus + settings_book)
# ---------------------------------------------------------------------------


def _make_bus(*, name: str | None, api_key: str | None, model: str | None) -> MagicMock:
    """Build a minimal ``Bus``-shaped mock with only settings_book wired."""
    bus = MagicMock()
    settings: dict[str, str] = {}
    if name is not None:
        settings[PROVIDER_NAME_KEY] = name
    if api_key is not None:
        settings[PROVIDER_API_KEY_KEY] = api_key
    if model is not None:
        settings[PROVIDER_MODEL_KEY] = model

    bus.settings_book.get_value = MagicMock(side_effect=lambda *, key: settings.get(key))
    bus.settings_book.set = MagicMock(
        side_effect=lambda *, key, value: settings.__setitem__(key, value)
    )
    return bus


def test_get_provider_returns_OpenAIProvider_when_configured():
    bus = _make_bus(name="openai", api_key="sk-test", model="gpt-4o-mini")
    provider = get_provider(bus=bus)
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-4o-mini"
    assert provider.api_key == "sk-test"


def test_get_provider_uses_call_model_override():
    bus = _make_bus(name="openai", api_key="sk-test", model="gpt-4o-mini")
    provider = get_provider(bus=bus, model="gpt-4o")
    assert provider.model == "gpt-4o"
