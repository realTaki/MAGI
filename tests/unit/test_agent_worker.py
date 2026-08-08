"""AgentWorker unit tests (design §10).

Covers:
  1. Single ChatJob → single LLM turn → no tools → delivery
  2. Single ChatJob → LLM returns tool_use → tool completed → second LLM → delivery
  3. Steering injection via claim_for_conversation
  4. Cancel path
  5. Context assembly (system_prompt delegation)
  6. Token usage recording
  7. Max iterations exceeded
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures — minimal RunContext + mocked bus
# ---------------------------------------------------------------------------


@pytest.fixture
def run_context():
    """Fresh RunContext for a single turn."""
    # Use a local import guard so the test file doesn't require the runtime
    # to be importable.
    from magi.agent.worker import RunContext
    return RunContext(
        run_id=f"test-run-{uuid.uuid4().hex[:8]}",
        root_event_id=f"evt-{uuid.uuid4().hex[:8]}",
        uid=42,
        session_id="sess-1",
        channel="tg",
        caller_role=None,
        conversation_id="conv-abc",
        max_iterations=5,
    )


@dataclass
class _FakeLLMResult:
    """Mimics CallLLMResult without importing the real DTO."""
    job_id: str = ""
    success: bool = True
    response: dict | None = None
    error: str | None = None
    error_code: str = ""
    token_usage: dict | None = None
    model: str = "claude:sonnet"


def _fake_llm_result(text: str = "", tool_uses: list | None = None, **kw):
    response = {"text": text, "tool_uses": tool_uses or [], "raw_blocks": []}
    return _FakeLLMResult(response=response, **kw)


def _make_mock_bus(**overrides):
    """Minimal bus mock with agent_turn_store.claim_root_and_acquire_turn."""
    bus = Mock()
    # default no-ops
    bus.agent_turn_store = Mock()
    bus.agent_turn_store.claim_root_and_acquire_turn.return_value = None
    bus.agent_turn_store.commit_terminal = Mock()
    bus.agent_turn_store.commit_terminal_failure = Mock()
    bus.agent_turn_store.commit_terminal_cancelled = Mock()
    bus.agent_turn_store.commit_waiting_effects = Mock(return_value=True)
    bus.agent_turn_store.renew_turn_lease = Mock(return_value=True)
    bus.agent_turn_store.is_cancel_requested = Mock(return_value=False)
    bus.agent_turn_store.request_cancel = Mock()
    bus.agent_turn_store.get = Mock(return_value=None)

    bus.llm_job_board = Mock()
    bus.llm_job_board.publish = Mock(return_value="llm-job-1")
    bus.llm_job_board.get_result = Mock(return_value=None)

    bus.tool_job_board = Mock()
    bus.tool_job_board.publish = Mock(return_value="tool-job-1")
    bus.tool_job_board.get_result = Mock(return_value=None)

    bus.a2a_job_board = Mock()
    bus.a2a_job_board.publish = Mock(return_value="a2a-job-1")
    bus.a2a_job_board.get_result = Mock(return_value=None)

    bus.delivery_job_board = Mock()
    bus.delivery_job_board.publish = Mock()

    bus.agent_job_board = Mock()
    bus.agent_job_board.claim = Mock(return_value=None)
    bus.agent_job_board.claim_for_conversation = Mock(return_value=None)
    bus.agent_job_board.release = Mock()
    bus.agent_job_board.submit_result = Mock()

    bus.sessions_book = Mock()
    bus.sessions_book.get_for_owner = Mock(return_value=None)

    bus.messages_book = Mock()
    bus.messages_book.list_for_session = Mock(return_value=[])

    bus.memory_book = Mock()
    bus.memory_book.list_by_owner = Mock(return_value=[])

    bus.contacts_book = Mock()
    bus.contacts_book.get = Mock(return_value=None)

    bus.contact_notes_book = Mock()
    bus.contact_notes_book.list_for_contact = Mock(return_value=[])
    bus.contact_notes_book.read_daily_note = Mock(return_value=None)

    bus.tool_definitions_book = Mock()
    bus.tool_definitions_book.list_enabled = Mock(return_value=[])

    bus.tool_catalog_book = Mock()
    bus.tool_catalog_book.get = Mock(return_value=None)

    bus.skills_book = Mock()
    bus.skills_book.list = Mock(return_value=[])

    bus.prompt_book = Mock()
    bus.prompt_book.get = Mock(return_value="You are a helpful assistant.")

    bus.token_usage_book = Mock()
    bus.token_usage_book.add = Mock()

    bus.settings_book = Mock()
    bus.settings_book.get = Mock(return_value=None)

    bus.memberships_book = None
    bus.stream_hub = Mock()
    bus.stream_hub.create = Mock()
    bus.stream_hub.get = Mock()
    bus.stream_hub.close = Mock()

    for k, v in overrides.items():
        setattr(bus, k, v)
    return bus


def _fake_chat_job(**kw):
    """Mimics ChatJob without importing the real DTO."""
    from magi.new_bus.guild.chatJob import ChatJob as _RealChatJob
    # Prefer the real dataclass so that isinstance checks pass.
    return _RealChatJob(
        event_id=kw.get("event_id", f"evt-{uuid.uuid4().hex[:8]}"),
        run_id=kw.get("run_id", f"run-{uuid.uuid4().hex[:8]}"),
        conversation_id=kw.get("conversation_id", "conv-abc"),
        kind=kw.get("kind", "chat"),
        payload=kw.get("payload", {"uid": 42, "text": "hello"}),
    )


def _fake_agent_turn(**kw):
    """Mimics AgentTurn."""
    turn = Mock()
    turn.run_id = kw.get("run_id", f"run-{uuid.uuid4().hex[:8]}")
    turn.root_event_id = kw.get("root_event_id", f"evt-{uuid.uuid4().hex[:8]}")
    turn.uid = kw.get("uid", 42)
    turn.session_id = kw.get("session_id", "sess-1")
    turn.channel = kw.get("channel", "tg")
    turn.conversation_id = kw.get("conversation_id", "conv-abc")
    turn.iteration = kw.get("iteration", 0)
    turn.messages_tail = kw.get("messages_tail", [])
    return turn


# ---------------------------------------------------------------------------
# Test 1: Single ChatJob → single LLM turn → no tools → delivery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_turn_no_tools_commits_reply(run_context):
    """Design §10.1: one ChatJob, one LLM call with text only → terminal."""
    from magi.agent.worker import AgentWorker

    text = "Hello from test!"
    bus = _make_mock_bus()
    bus.llm_job_board.get_result.return_value = _fake_llm_result(text=text)

    turn = _fake_agent_turn(
        run_id=run_context.run_id,
        conversation_id=run_context.conversation_id,
    )
    bus.agent_turn_store.claim_root_and_acquire_turn.return_value = (
        _fake_chat_job(), turn,
    )

    worker = AgentWorker(bus=bus, poll_seconds=0.01)
    # Don't start the full loop; call _process directly
    run_context.messages = []  # no history
    await worker._process(run_context)

    # assert: terminal path was taken
    assert run_context.final_reply == text
    assert run_context.final_error is None
    bus.agent_turn_store.commit_terminal.assert_called_once()
    called_kwargs = bus.agent_turn_store.commit_terminal.call_args.kwargs
    assert called_kwargs["run_id"] == run_context.run_id
    assert called_kwargs["assistant_text"] == text


# ---------------------------------------------------------------------------
# Test 2: LLM returns tool_use → tool completed → second LLM → delivery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_loop_completes_and_delivers(run_context):
    """Design §10.2: agent loop with tools → gather → second LLM → terminal."""
    from magi.agent.worker import AgentWorker
    from magi.new_bus.guild.runToolJob import RunToolResult

    bus = _make_mock_bus()

    # First LLM: returns a tool_use
    tool_call_id = "tool-1"
    llm1 = _fake_llm_result(
        text="Let me check...",
        tool_uses=[{"name": "search", "id": tool_call_id, "input": {"q": "test"}}],
    )
    # Second LLM: returns terminal text
    llm2 = _fake_llm_result(text="Found something.")
    bus.llm_job_board.get_result.side_effect = [llm1, llm2]

    # Tool result
    tool_result = RunToolResult(
        job_id="tool-job-1", success=True, content="search result",
        is_error=False, tool_call_id=tool_call_id,
    )
    bus.tool_job_board.get_result.return_value = tool_result

    turn = _fake_agent_turn(
        run_id=run_context.run_id,
        conversation_id=run_context.conversation_id,
    )

    worker = AgentWorker(bus=bus, poll_seconds=0.01)
    run_context.messages = []
    await worker._process(run_context)

    assert run_context.final_reply == "Found something."
    assert run_context.final_error is None
    # Tool was published
    assert bus.tool_job_board.publish.called
    # Two LLM calls were made
    assert bus.llm_job_board.publish.call_count == 2


# ---------------------------------------------------------------------------
# Test 3: Steering injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_steering_injected_during_gather(run_context):
    """Design §10.3: tool loop with parallel steering → steering text in messages."""
    from magi.agent.worker import AgentWorker
    from magi.new_bus.guild.runToolJob import RunToolResult

    bus = _make_mock_bus()

    # LLM returns tool_use, enters gather phase
    tool_call_id = "tool-1"
    llm1 = _fake_llm_result(
        text="Checking...",
        tool_uses=[{"name": "search", "id": tool_call_id, "input": {"q": "test"}}],
    )
    llm2 = _fake_llm_result(text="Answer with context.")
    bus.llm_job_board.get_result.side_effect = [llm1, llm2]

    # Tool result
    bus.tool_job_board.get_result.return_value = RunToolResult(
        job_id="tool-job-1", success=True, content="result",
        is_error=False, tool_call_id=tool_call_id,
    )

    # Steering: claim_for_conversation returns a ChatJob on first call
    steer_job = _fake_chat_job(
        event_id="steer-ev-1",
        conversation_id=run_context.conversation_id,
        payload={"uid": 42, "text": "Also check this please."},
    )
    bus.agent_job_board.claim_for_conversation.side_effect = [steer_job, None, None]

    turn = _fake_agent_turn(
        run_id=run_context.run_id,
        conversation_id=run_context.conversation_id,
    )

    worker = AgentWorker(bus=bus, poll_seconds=0.01)
    run_context.messages = []
    await worker._process(run_context)

    # Steering text was collected
    assert run_context.pending_steering_event_ids == ["steer-ev-1"]
    # Messages should include the steering in a user message
    steering_found = any(
        "Also check this" in str(m.get("content", ""))
        for m in run_context.messages
    )
    assert steering_found


# ---------------------------------------------------------------------------
# Test 4: Cancel path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_interrupts_process(run_context):
    """Design §10.4: is_cancel_requested returns True → process exits cancelled."""
    from magi.agent.worker import AgentWorker

    bus = _make_mock_bus()
    bus.agent_turn_store.is_cancel_requested.return_value = True

    turn = _fake_agent_turn(
        run_id=run_context.run_id,
        conversation_id=run_context.conversation_id,
    )

    worker = AgentWorker(bus=bus, poll_seconds=0.01)
    run_context.messages = []
    await worker._process(run_context)

    assert run_context.cancelled is True
    bus.agent_turn_store.commit_terminal_cancelled.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5: Context assembly via system_prompt delegation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_system_prompt_delegates_to_module(run_context):
    """Worker._system_prompt calls build_system_prompt from the migrated module."""
    from magi.agent.worker import AgentWorker

    bus = _make_mock_bus()
    # Memory rows with correct field names (Memory.subject, Memory.body)
    from dataclasses import dataclass as _dc

    @_dc
    class _FakeMemory:
        kind: str = "fact"
        subject: str = "User likes Python"
        body: str = "Prefers type hints"

    bus.memory_book.list_by_owner.return_value = [_FakeMemory()]
    bus.prompt_book.get.return_value = "You are MAGI."

    worker = AgentWorker(bus=bus)
    result = worker._system_prompt(run_context)

    assert "You are MAGI" in result
    assert "fact" in result.lower() or "User likes Python" in result


# ---------------------------------------------------------------------------
# Test 6: Token usage recording
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_usage_recorded_on_llm_success(run_context):
    """_record_token_usage calls token_usage_book.add when usage is present."""
    from magi.agent.worker import AgentWorker

    bus = _make_mock_bus()
    worker = AgentWorker(bus=bus)

    result = _fake_llm_result(
        text="ok",
        token_usage={"input_tokens": 100, "output_tokens": 50},
        model="claude:sonnet",
    )
    worker._record_token_usage(run_context, result)

    bus.token_usage_book.add.assert_called_once()
    call_kwargs = bus.token_usage_book.add.call_args.kwargs
    assert call_kwargs["uid"] == 42
    assert call_kwargs["model"] == "claude:sonnet"


# ---------------------------------------------------------------------------
# Test 7: Max iterations exceeded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_iterations_exceeded_fails_gracefully(run_context):
    """Agent loop stops at max_iterations without crashing."""
    from magi.agent.worker import AgentWorker
    from magi.new_bus.guild.runToolJob import RunToolResult

    run_context.max_iterations = 2

    bus = _make_mock_bus()
    # Every LLM call returns a tool_use → loop never terminates naturally
    llm = _fake_llm_result(
        text="let me check",
        tool_uses=[{"name": "search", "id": "t1", "input": {}}],
    )
    bus.llm_job_board.get_result.return_value = llm
    bus.tool_job_board.get_result.return_value = RunToolResult(
        job_id="j1", success=True, content="r", is_error=False, tool_call_id="t1",
    )

    turn = _fake_agent_turn(
        run_id=run_context.run_id,
        conversation_id=run_context.conversation_id,
    )

    worker = AgentWorker(bus=bus, poll_seconds=0.01)
    run_context.messages = []

    # Should not raise
    await worker._process(run_context)

    # After loop, commit_terminal_failure should be called
    assert bus.agent_turn_store.commit_terminal_failure.called
    call_kwargs = bus.agent_turn_store.commit_terminal_failure.call_args.kwargs
    assert call_kwargs["error_code"] == "max_iterations_exceeded"
