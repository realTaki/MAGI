"""AgentWorker unit tests (design §8).

Covers:
  1. Single ChatNotifyJob → single LLM turn → no tools → delivery
  2. Single ChatNotifyJob → LLM returns tool_use → tool completed → second LLM → delivery
  3. Steering injection via claim_for_steering
  4. Cancel path (cancel_event)
  5. Context assembly (system_prompt delegation)
  6. Token usage recording
  7. Max iterations exceeded
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock

import pytest

from bus.bases.job import JobStatus

# ---------------------------------------------------------------------------
# Minimal fakes (no runtime import needed)
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    """Mimics Session DTO."""

    conversation_id: int = 1
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
    job_id: int = 1
    status: JobStatus = JobStatus.COMPLETED
    response: dict | None = None
    error: str | None = None
    error_code: str = ""
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
    bus.agent_job_board.claim_for_new_conversation = Mock(return_value=None)
    bus.agent_job_board.submit_result = Mock()
    bus.agent_job_board.claim_for_steering = Mock(return_value=None)

    bus.llm_job_board = Mock()
    bus.llm_job_board.publish = Mock(return_value="llm-job-1")
    bus.llm_job_board.get_result = Mock(return_value=None)
    bus.llm_job_board.wait_for_result = AsyncMock(
        side_effect=lambda **_kwargs: bus.llm_job_board.get_result()
    )

    bus.tool_job_board = Mock()
    bus.tool_job_board.publish = Mock(return_value="tool-job-1")
    bus.tool_job_board.get_result = Mock(return_value=None)

    bus.a2a_request_job_board = None
    bus.a2a_notify_job_board = None

    bus.delivery_notify_job_board = Mock()
    bus.delivery_notify_job_board.publish = Mock()

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
    bus.tool_catalog_book.get_current = Mock(return_value=None)

    bus.skills_book = Mock()
    bus.skills_book.list = Mock(return_value=[])

    bus.prompt_book = Mock()
    bus.prompt_book.get = Mock(return_value="You are a helpful assistant.")

    bus.token_usage_book = Mock()
    bus.token_usage_book.add = Mock()

    bus.settings_book = Mock()
    bus.settings_book.get_value = Mock(return_value=None)

    bus.memberships_book = None

    for k, v in overrides.items():
        setattr(bus, k, v)
    return bus


# ---------------------------------------------------------------------------
# Test 1: single turn, no tools → delivery published, ChatNotifyResult success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_turn_no_tools_delivers():
    from agent.worker import AgentWorker, RunContext

    bus = _make_bus()
    bus.llm_job_board.get_result.return_value = _fake_llm(text="Hello!")

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        conversation_id=1,
    )
    ctx.messages = []

    worker = AgentWorker(bus=bus)
    await worker._process(ctx)

    # reply set and delivery published
    assert ctx.final_reply == "Hello!"
    assert ctx.failed is False
    bus.delivery_notify_job_board.publish.assert_called_once()
    # ChatNotifyResult is submitted by _run(), not _process();
    # inside _process we only test side-effects are correct.


@pytest.mark.asyncio
async def test_missing_provider_delivers_actionable_reply() -> None:
    """A failed CallLLMJob still becomes a channel delivery, never a blank chat.

    The agent forwards ``error_code: error`` verbatim — no agent-side
    paraphrase. The "is this too detailed for the user" call belongs
    upstream in :mod:`providers.errors`, not in the agent worker.
    """
    from agent.worker import AgentWorker, RunContext
    from bus.firmwares.jobs.callLLMJob import LLMErrorCode

    bus = _make_bus()
    bus.llm_job_board.get_result.return_value = _fake_llm(
        status=JobStatus.FAILED,
        error_code=LLMErrorCode.CREDENTIALS_REQUIRED,
        error="MAGI runtime has no LLM provider configured",
    )
    ctx = RunContext(contact_id=42, channel="webui", conversation_id=1)

    await AgentWorker(bus=bus)._process(ctx)

    assert ctx.failed is True
    assert ctx.final_reply == (
        f"{LLMErrorCode.CREDENTIALS_REQUIRED}: MAGI runtime has no LLM provider configured"
    )
    delivery = bus.delivery_notify_job_board.publish.call_args.args[0]
    assert delivery.channel == "webui"
    assert delivery.conversation_id == 1
    assert delivery.text == ctx.final_reply


# ---------------------------------------------------------------------------
# Test 2: LLM returns tool_use → second LLM call → delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_loop_completes():
    from agent.worker import AgentWorker, RunContext
    from bus.firmwares.jobs.runToolJob import RunToolResult

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
        job_id=1,
        status=JobStatus.COMPLETED,
        content="result",
        tool_call_id="tc-1",
    )

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        conversation_id=1,
    )
    ctx.messages = []

    worker = AgentWorker(bus=bus)
    await worker._process(ctx)

    assert ctx.final_reply == "Found it."
    assert bus.tool_job_board.publish.called
    assert bus.llm_job_board.publish.call_count == 2


# ---------------------------------------------------------------------------
# Test 3: steering via claim_for_steering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steering_injected():
    from agent.worker import AgentWorker, RunContext
    from bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob
    from bus.firmwares.jobs.runToolJob import RunToolResult

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
        job_id=1,
        status=JobStatus.COMPLETED,
        content="result",
        tool_call_id="tc-1",
    )

    steer_job = ChatNotifyJob(
        conversation_id=1,
        contact_id=42,
        text="Also check this please.",
    )
    object.__setattr__(steer_job, "job_id", 1)  # init=False，frozen 下回填
    bus.agent_job_board.claim_for_steering.side_effect = [steer_job, None, None]

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        conversation_id=1,
    )
    ctx.messages = []

    worker = AgentWorker(bus=bus)
    await worker._process(ctx)

    # steering text should appear in messages
    steering_found = any("Also check this" in str(m.get("content", "")) for m in ctx.messages)
    assert steering_found
    # steering ChatNotifyJob was consumed (submitted as ChatNotifyResult)
    from bus.firmwares.jobs.chatNotifyJob import ChatNotifyResult

    bus.agent_job_board.submit_result.assert_any_call(
        job_id=1,
        worker_id=worker.worker_id,
        result=ChatNotifyResult(job_id=1, status=JobStatus.COMPLETED),
    )


@pytest.mark.asyncio
async def test_one_agent_worker_consumes_persisted_steering_while_waiting_for_a_tool():
    """A single AgentWorker must consume a same-conversation turn in-band.

    The test uses the real durable ChatNotify board: no second AgentWorker and
    no release/reclaim hand-off are involved.  ``_gather_all`` sees the new
    turn while a tool result is pending, incorporates its text, and settles
    the steering job under that same worker's lease.
    """
    from types import SimpleNamespace

    from agent.worker import AgentWorker, RunContext
    from bus.bases.db.engine import EngineFactory
    from bus.bases.job import JobStatus
    from bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob, chatNotifyBoard
    from bus.firmwares.jobs.runToolJob import RunToolResult

    factory = EngineFactory("sqlite:///:memory:")
    agent_board = chatNotifyBoard(factory)
    # This focused test requires only the chat queue.  Creating global
    # metadata would also include the A2A tables, whose MAGIS FK target is
    # deliberately absent from this local fixture.
    agent_board.job_model.__table__.create(factory.engine)
    steering_job_id = agent_board.publish(
        ChatNotifyJob(conversation_id=42, contact_id=7, channel="tg", text="补充一下这个约束")
    )
    tool_result = RunToolResult(
        job_id=99,
        status=JobStatus.COMPLETED,
        content="tool complete",
        tool_call_id="tool-call-1",
    )
    bus = SimpleNamespace(
        agent_job_board=agent_board,
        tool_job_board=SimpleNamespace(get_result=lambda **_kwargs: tool_result),
        a2a_request_job_board=None,
        settings_book=SimpleNamespace(get_value=lambda **_kwargs: None),
    )
    worker = AgentWorker(bus=bus)  # type: ignore[arg-type]

    async def direct_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    worker.call = direct_call  # type: ignore[method-assign]
    ctx = RunContext(contact_id=7, channel="tg", conversation_id=42)
    gathered = await worker._gather_all(
        ctx,
        {"tool-call-1": 99},
        {},
        {},
    )

    assert gathered is not None
    assert gathered.steering_text == "补充一下这个约束"
    settled = agent_board.get_result(job_id=steering_job_id)
    assert settled is not None
    assert settled.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_agent_worker_runs_different_conversations_concurrently():
    """One AgentWorker uses its slots across conversations, never per process."""
    from types import SimpleNamespace

    from agent.worker import AgentWorker
    from bus.bases.db.engine import EngineFactory
    from bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob, chatNotifyBoard

    factory = EngineFactory("sqlite:///:memory:")
    agent_board = chatNotifyBoard(factory)
    agent_board.job_model.__table__.create(factory.engine)
    agent_board.publish(ChatNotifyJob(conversation_id=1, contact_id=1, channel="tg", text="one"))
    agent_board.publish(ChatNotifyJob(conversation_id=2, contact_id=2, channel="tg", text="two"))
    bus = SimpleNamespace(
        agent_job_board=agent_board,
        a2a_request_job_board=None,
        a2a_notify_job_board=None,
        settings_book=SimpleNamespace(get_value=lambda **_kwargs: None),
    )
    worker = AgentWorker(bus=bus, concurrency=2)  # type: ignore[arg-type]

    async def direct_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    both_started = asyncio.Event()
    release = asyncio.Event()
    seen: list[int] = []

    async def process(ctx):
        seen.append(ctx.conversation_id)
        if len(seen) == 2:
            both_started.set()
        await release.wait()

    worker.call = direct_call  # type: ignore[method-assign]
    worker._process = process  # type: ignore[method-assign]
    run_task = asyncio.create_task(worker._run())
    await asyncio.wait_for(both_started.wait(), timeout=1)
    assert set(seen) == {1, 2}
    worker._stopping = True
    release.set()
    await asyncio.wait_for(run_task, timeout=1)


# ---------------------------------------------------------------------------
# Test 4: cancel via cancel_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_interrupts():
    from agent.worker import AgentWorker, RunContext

    bus = _make_bus()

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        conversation_id=1,
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
    from agent.worker import AgentWorker, RunContext

    bus = _make_bus()
    bus.memory_book.list_by_owner.return_value = [_FakeMemory()]
    bus.contacts_book.get.return_value = _FakeContact(name="TestUser")
    bus.prompt_book.get.side_effect = lambda *, key: {
        "agent/soul": "You are MAGI.",
    }.get(key)
    bus.contact_notes_book.list_for_contact.return_value = []
    bus.contact_notes_book.read_daily_note.return_value = None
    bus.skills_book.list.return_value = []

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        conversation_id=1,
    )

    worker = AgentWorker(bus=bus)
    prompt = await worker._system_prompt(ctx)

    assert "MAGI" in prompt
    assert "fact" in prompt.lower() or "Python" in prompt


@pytest.mark.asyncio
async def test_shutdown_marks_claimed_agent_job_cancelled():
    """A shutdown-cancelled turn must not settle its claimed event as success."""
    from types import SimpleNamespace

    from agent.worker import AgentWorker

    bus = _make_bus()
    job = SimpleNamespace(
        job_id="shutdown-job",
        conversation_id=1,
        contact_id=42,
        channel="tg",
        text="hello",
    )
    claimed = False

    def claim():
        nonlocal claimed
        if claimed:
            return None
        claimed = True
        return job

    bus.agent_job_board.claim_for_new_conversation.side_effect = lambda **_kwargs: claim()
    worker = AgentWorker(bus, concurrency=1)
    started = asyncio.Event()

    async def blocked_process(_ctx, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    worker._process = blocked_process  # type: ignore[method-assign]
    await worker.start()
    await started.wait()
    await worker.stop()

    result = bus.agent_job_board.submit_result.call_args.kwargs["result"]
    assert result.status == JobStatus.FAILED
    assert result.error is None


# ---------------------------------------------------------------------------
# Test 7: max iterations exceeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_iterations_exceeded():
    from agent.worker import AgentWorker, RunContext
    from bus.firmwares.jobs.runToolJob import RunToolResult

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
        job_id=1,
        status=JobStatus.COMPLETED,
        content="r",
        tool_call_id="tc-1",
    )

    ctx = RunContext(
        contact_id=42,
        channel="tg",
        conversation_id=1,
        max_iterations=2,
    )
    ctx.messages = []

    worker = AgentWorker(bus=bus)
    await worker._process(ctx)

    assert "已达到最大工具调用次数" in ctx.final_reply
    bus.delivery_notify_job_board.publish.assert_called()
