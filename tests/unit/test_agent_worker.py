"""AgentWorker unit tests (design §8).

Covers:
  1. Single ChatJob → single LLM turn → no tools → delivery
  2. Single ChatJob → LLM returns tool_use → tool completed → second LLM → delivery
  3. Steering injection via claim_for_conversation
  4. Cancel path (cancel_event)
  5. Context assembly (system_prompt delegation)
  6. Token usage recording
  7. Max iterations exceeded
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import Mock

import pytest

# ---------------------------------------------------------------------------
# Minimal fakes (no runtime import needed)
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    """Mimics Session DTO."""

    conversation_id: str = "sess-1"
    uid: int = 42
    delivery_address: str = "tg:123"


@dataclass
class _FakeMsg:
    """Mimics Message DTO."""

    role: str = "user"
    text: str = "hello"


@dataclass
class _FakeMemory:
    kind: str = "fact"
    subject: str = "User likes Python"
    body: str = "Prefers type hints"


@dataclass
class _FakeContact:
    name: str = "TestUser"
    display_name: str | None = None


@dataclass
class _FakeContactNote:
    kind: str = "permanent"
    note: str = "some note"


@dataclass
class _FakeToolDef:
    name: str = "search"
    description: str = "Search the web"
    input_schema: dict | None = None

    def __post_init__(self):
        if self.input_schema is None:
            self.input_schema = {"type": "object", "properties": {}}


@dataclass
class _FakeLLMResult:
    job_id: str = "llm-1"
    success: bool = True
    response: dict | None = None
    error: str | None = None
    error_code: str = ""
    token_usage: dict | None = None
    model: str = "claude:sonnet"


def _fake_llm(text: str = "", tool_uses: list | None = None, **kw) -> _FakeLLMResult:
    return _FakeLLMResult(
        response={"text": text, "tool_uses": tool_uses or [], "raw_blocks": []},
        **kw,
    )


def _make_bus(**overrides) -> Mock:
    """Mock bus with all job boards and books used by AgentWorker."""
    bus = Mock()

    # -- job boards --
    bus.agent_job_board = Mock()
    bus.agent_job_board.claim = Mock(return_value=None)
    bus.agent_job_board.release = Mock()
    bus.agent_job_board.submit_result = Mock()
    bus.agent_job_board.claim_for_conversation = Mock(return_value=None)

    bus.llm_job_board = Mock()
    bus.llm_job_board.publish = Mock(return_value="llm-job-1")
    bus.llm_job_board.get_result = Mock(return_value=None)

    bus.tool_job_board = Mock()
    bus.tool_job_board.publish = Mock(return_value="tool-job-1")
    bus.tool_job_board.get_result = Mock(return_value=None)

    bus.a2a_request_job_board = None
    bus.a2a_notify_job_board = None

    bus.delivery_job_board = Mock()
    bus.delivery_job_board.publish = Mock()

    # -- books --
    bus.conversations_book = Mock()
    bus.conversations_book.get_for_owner = Mock(return_value=None)

    bus.messages_book = Mock()
    bus.messages_book.list_for_conversation = Mock(return_value=[])

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

    for k, v in overrides.items():
        setattr(bus, k, v)
    return bus


# ---------------------------------------------------------------------------
# Test 1: single turn, no tools → delivery published, ChatJobResult success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_turn_no_tools_delivers():
    from magi.agent.worker import AgentWorker, RunContext

    bus = _make_bus()
    bus.llm_job_board.get_result.return_value = _fake_llm(text="Hello!")

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        caller_role=None,
        conversation_id="conv-1",
    )
    ctx.messages = []

    worker = AgentWorker(bus=bus)
    await worker._process(ctx)

    # reply set and delivery published
    assert ctx.final_reply == "Hello!"
    assert ctx.final_error is None
    bus.delivery_job_board.publish.assert_called_once()
    # ChatJobResult is submitted by _run(), not _process();
    # inside _process we only test side-effects are correct.


# ---------------------------------------------------------------------------
# Test 2: LLM returns tool_use → second LLM call → delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_loop_completes():
    from magi.agent.worker import AgentWorker, RunContext
    from magi.bus.guild.runToolJob import RunToolResult

    bus = _make_bus()

    llm1 = _fake_llm(
        text="Checking...",
        tool_uses=[
            {"name": "search", "id": "tc-1", "input": {"q": "test"}},
        ],
    )
    llm2 = _fake_llm(text="Found it.")
    bus.llm_job_board.get_result.side_effect = [llm1, llm2]

    bus.tool_job_board.get_result.return_value = RunToolResult(
        job_id="tj-1",
        success=True,
        content="result",
        is_error=False,
        tool_call_id="tc-1",
    )

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        caller_role=None,
        conversation_id="conv-1",
    )
    ctx.messages = []

    worker = AgentWorker(bus=bus)
    await worker._process(ctx)

    assert ctx.final_reply == "Found it."
    assert bus.tool_job_board.publish.called
    assert bus.llm_job_board.publish.call_count == 2


# ---------------------------------------------------------------------------
# Test 3: steering via claim_for_conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steering_injected():
    from magi.agent.worker import AgentWorker, RunContext
    from magi.bus.guild.chatJob import ChatJob
    from magi.bus.guild.runToolJob import RunToolResult

    bus = _make_bus()

    llm1 = _fake_llm(
        text="Checking...",
        tool_uses=[
            {"name": "search", "id": "tc-1", "input": {}},
        ],
    )
    llm2 = _fake_llm(text="Answer with steering context.")
    bus.llm_job_board.get_result.side_effect = [llm1, llm2]

    bus.tool_job_board.get_result.return_value = RunToolResult(
        job_id="tj-1",
        success=True,
        content="result",
        is_error=False,
        tool_call_id="tc-1",
    )

    steer_job = ChatJob(
        job_id="steer-1",
        conversation_id="conv-1",
        payload={"contact_id": 42, "text": "Also check this please."},
    )
    bus.agent_job_board.claim_for_conversation.side_effect = [steer_job, None, None]

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        caller_role=None,
        conversation_id="conv-1",
    )
    ctx.messages = []

    worker = AgentWorker(bus=bus)
    await worker._process(ctx)

    # steering text should appear in messages
    steering_found = any("Also check this" in str(m.get("content", "")) for m in ctx.messages)
    assert steering_found
    # steering ChatJob was consumed (submitted as ChatJobResult)
    from magi.bus.guild.chatJob import ChatJobResult

    bus.agent_job_board.submit_result.assert_any_call(
        key="steer-1",
        result=ChatJobResult(job_id="steer-1", success=True, status="completed"),
    )


# ---------------------------------------------------------------------------
# Test 4: cancel via cancel_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_interrupts():
    from magi.agent.worker import AgentWorker, RunContext

    bus = _make_bus()

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        caller_role=None,
        conversation_id="conv-1",
    )
    ctx.messages = []
    ctx.cancel_event.set()  # simulate cancel

    worker = AgentWorker(bus=bus)
    await worker._process(ctx)

    assert ctx.cancelled is True
    # no LLM calls made
    bus.llm_job_board.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: system_prompt delegation (integration-style)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_delegates():
    from magi.agent.worker import AgentWorker, RunContext

    bus = _make_bus()
    bus.memory_book.list_by_owner.return_value = [_FakeMemory()]
    bus.contacts_book.get.return_value = _FakeContact(name="TestUser")
    bus.prompt_book.soul.return_value = "You are MAGI."
    bus.prompt_book.fallback_persona.return_value = "fallback"
    bus.contact_notes_book.list_for_contact.return_value = []
    bus.contact_notes_book.read_daily_note.return_value = None
    bus.skills_book.list.return_value = []

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        caller_role=None,
        conversation_id="conv-1",
    )

    worker = AgentWorker(bus=bus)
    prompt = await worker._system_prompt(ctx)

    assert "MAGI" in prompt
    assert "fact" in prompt.lower() or "Python" in prompt


# ---------------------------------------------------------------------------
# Test 6: token usage recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_usage_recorded():
    from magi.agent.worker import AgentWorker, RunContext

    bus = _make_bus()
    worker = AgentWorker(bus=bus)
    ctx = RunContext(
        contact_id=42,
        channel="tg",
        caller_role=None,
        conversation_id="conv-1",
    )

    result = _fake_llm(
        text="ok",
        token_usage={"input_tokens": 100, "output_tokens": 50},
        model="claude:sonnet",
    )
    await worker._record_token_usage(ctx, result)

    bus.token_usage_book.add.assert_called_once()
    # Assert the argument shape — the previous regression where
    # ``Book.add`` was silently getting a ``TypeError`` because the
    # adapter passed ``usage=``/``channel=`` keys the new signature
    # doesn't recognise would have slipped past ``assert_called_once``
    # alone (the call still happens, just inside the swallowed
    # ``except``). These assertions catch the next refactor of this
    # boundary.
    call_kwargs = bus.token_usage_book.add.call_args.kwargs
    assert call_kwargs["contact_id"] == 42
    assert call_kwargs["provider"] == "claude"
    assert call_kwargs["model"] == "claude:sonnet"
    assert call_kwargs["input_tokens"] == 100
    assert call_kwargs["output_tokens"] == 50
    assert call_kwargs["extra"] == {"channel": "tg"}


@pytest.mark.asyncio
async def test_shutdown_marks_claimed_agent_job_cancelled():
    """A shutdown-cancelled turn must not settle its claimed event as success."""
    from types import SimpleNamespace

    from magi.agent.worker import AgentWorker

    bus = _make_bus()
    job = SimpleNamespace(
        job_id="shutdown-job",
        run_id="run-1",
        conversation_id="conv-1",
        payload={},
    )
    claimed = False

    def claim():
        nonlocal claimed
        if claimed:
            return None
        claimed = True
        return job

    bus.agent_job_board.claim.side_effect = claim
    worker = AgentWorker(bus)
    started = asyncio.Event()

    async def blocked_process(_ctx):
        started.set()
        await asyncio.Event().wait()

    worker._process = blocked_process  # type: ignore[method-assign]
    await worker.start()
    await started.wait()
    await worker.stop()

    result = bus.agent_job_board.submit_result.call_args.kwargs["result"]
    assert result.success is False
    assert result.error_code == "magi.run_cancelled"


# ---------------------------------------------------------------------------
# Test 7: max iterations exceeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_iterations_exceeded():
    from magi.agent.worker import AgentWorker, RunContext
    from magi.bus.guild.runToolJob import RunToolResult

    bus = _make_bus()

    llm = _fake_llm(
        text="loop",
        tool_uses=[
            {"name": "search", "id": "tc-1", "input": {}},
        ],
    )
    # always returns a tool_use → never terminates naturally
    bus.llm_job_board.get_result.return_value = llm
    bus.tool_job_board.get_result.return_value = RunToolResult(
        job_id="tj-1",
        success=True,
        content="r",
        is_error=False,
        tool_call_id="tc-1",
    )

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        caller_role=None,
        conversation_id="conv-1",
        max_iterations=2,
    )
    ctx.messages = []

    worker = AgentWorker(bus=bus)
    await worker._process(ctx)

    assert "已达到最大工具调用次数" in ctx.final_reply
    bus.delivery_job_board.publish.assert_called()
