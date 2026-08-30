"""AgentWorker — bus 上唯一的 agent turn consumer.

设计原则（与 :class:`~tools.worker.ToolsWorker` 、
:class:`~providers.worker.ProvidersWorker` 对齐）：

- **只依赖 bus**。老的 ``bus`` store / facade 一概不碰。
- **构造靠注入**。``AgentWorker(bus: Bus)`` 由 composition root 显式注入。
- **board claim steering**：steering 不通过进程内队列，而是在
  ``_gather_all`` 中主动 ``claim_for_steering`` 认领同 session 的新
  ChatNotifyJob。board 本身是唯一持久化协调点。
- **ChatNotify 的接收回执是 ``PROCESSING``**：channel 观察 claim 写下的
  durable lease，而不等待整轮执行的终态。
- **回复走 delivery notify board**：回复文本统一由 ``_publish_delivery`` 投递。

本步骤已完成 Phase 2 子模块迁移，现已委托调用：
- ``system_prompt.build_system_prompt(bus=...)``
- ``agent_context.build_messages_from_conversation(bus=...)``
- ``auto_title.request_conversation_title(bus=...)``
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from old_bus.bases.job import JobStatus
from runtime_worker import RuntimeWorker

if TYPE_CHECKING:
    from old_bus import Bus
    from old_bus.firmwares.jobs.callLLMJob import CallLLMResult
    from old_bus.firmwares.jobs.runToolJob import RunToolJob

logger = logging.getLogger("agent.worker")

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

_MAX_STEERING_PARTS = 16
_DEFAULT_MAX_ITERATIONS = 10
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_TOOL_WAIT_SECONDS = 300.0
_DEFAULT_LLM_TIMEOUT_SECONDS = 120.0

# A2A is a MAGIS-shared durable collaboration plane.  It is deliberately
# not a human channel or an HTTP transport.
_A2A_ENABLED = True


# ---------------------------------------------------------------------------
# RunContext
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    contact_id: int | None
    channel: str
    conversation_id: int = 0  # chat_conversations.id；0 = 无会话（transcript 不可用时）
    messages: list[dict] = field(default_factory=list)
    max_iterations: int = _DEFAULT_MAX_ITERATIONS
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    final_reply: str = ""
    failed: bool = False  # 失败标志（succeeded = not failed）
    cancelled: bool = False
    a2a_kind: str | None = None
    a2a_source_magi_id: int | None = None
    a2a_job_id: int | None = None
    # The current claimed A2A text is normally already the final user row in
    # the local transcript.  Keep it separately as a resilience fallback if
    # that best-effort transcript write was unavailable.
    a2a_inbound_text: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _SplitJobs:
    # ``tool_jobs`` carries :class:`RunToolJob` (which keeps its own
    # ``tool_call_id``); A2A jobs no longer carry ``tool_call_id`` on the
    # wire, so we pair each one with the LLM tool_call_id it came from
    # here — downstream ``_publish_effects`` / ``_gather_all`` both want
    # ``tool_call_id`` as the dict key.
    tool_jobs: list = field(default_factory=list)
    a2a_request_jobs: list[tuple[str, Any]] = field(default_factory=list)
    a2a_notify_jobs: list[tuple[str, Any]] = field(default_factory=list)


@dataclass
class _GatherResult:
    tool_results: dict[str, Any] = field(default_factory=dict)
    a2a_results: dict[str, Any] = field(default_factory=dict)
    steering_text: str | None = None


# ---------------------------------------------------------------------------
# AgentWorker
# ---------------------------------------------------------------------------


class AgentWorker(RuntimeWorker):
    """Concurrent-by-conversation consumer of one MAGI's turn streams."""

    worker_name = "agent"

    def __init__(
        self,
        bus: Bus,
        *,
        poll_seconds: float = 0.25,
        magi_id: int | None = None,
        concurrency: int | None = None,
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds, concurrency=concurrency)
        # ``self.worker_id`` 已经在 :class:`RuntimeWorker.__init__` 里生成
        # (``f"{self.worker_name}-{uuid.uuid4().hex}"``), 此处不用再赋一次
        # —— 覆盖会丢掉原 UUID 又生成一个新 UUID, 白消耗一次熵。
        self._in_flight: dict[int, asyncio.Event] = {}  # conversation_id → cancel_event
        # ``magi_id`` is the runtime's own ``magis_memberships.id`` —
        # propagated in from :class:`WorkerRegistry`, which reads it
        # from the provisioned RuntimeSpec at boot
        # (:mod:`startup.runtime`). Used by
        # :meth:`_system_prompt` to render the per-MAGI instruction
        # block (team + role layers from MAGIS Books); ``None``
        # short-circuits that lookup and renders only the personal
        # instruction.
        self._magi_id = magi_id
        self._claim_cursor = 0
        self._claim_lock = asyncio.Lock()
        self._active_a2a_peers: set[int] = set()
        self._managed_contexts: set[int] = set()

    async def on_start(self) -> None:
        """Register AgentWorker-owned defaults before consuming turns."""
        from agent.prompt_defaults import ensure_agent_prompt_defaults

        await self.call(ensure_agent_prompt_defaults, self.bus.prompt_book)

    # -- main loop -----------------------------------------------------------

    async def _run(self) -> None:
        """Run one claim loop per local execution slot.

        The shared claim lock reserves a conversation in ``_in_flight`` before
        another loop can select it.  Thus different conversations make
        progress concurrently while a same-conversation message remains
        available for in-band steering.
        """
        await asyncio.gather(*(self._run_consumer() for _ in range(self.concurrency)))

    async def _run_consumer(self) -> None:
        from old_bus.firmwares.jobs.chatNotifyJob import ChatNotifyResult

        while not self._stopping:
            source, job = await self._claim_next_turn()
            if job is None or source is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            conversation_id = getattr(job, "conversation_id", None) or 0

            is_a2a = source != "chat"
            a2a_inbound_text = ""
            if is_a2a:
                # A2A rows are MAGIS-shared, while transcripts live in the
                # receiving MAGI's local database.  Resolve the same local
                # per-peer header used by the board's claim write; the old
                # synthetic ``a2a:<source>`` string is not a valid local
                # conversation primary key.
                peer_magi_id = getattr(job, "source_magi_id", 0)
                a2a_inbound_text = getattr(job, "text", "")
                try:
                    conversation = await self.call(
                        self.bus.conversations_book.get_or_create_for_a2a_peer,
                        peer_magi_id=peer_magi_id,
                    )
                    conversation_id = conversation.id
                except Exception:
                    # The claim remains consumable if a local transcript
                    # store is temporarily unavailable.  ``_load_history``
                    # will use ``a2a_inbound_text`` below, preserving the
                    # pre-transcript one-turn behaviour until recovery.
                    logger.warning(
                        "A2A transcript header unavailable for peer=%s; "
                        "processing current turn without history",
                        peer_magi_id,
                        exc_info=True,
                    )
                    conversation_id = 0
            ctx = RunContext(
                contact_id=(None if is_a2a else job.contact_id),
                conversation_id=(conversation_id or 0),
                channel=(source if is_a2a else job.channel),
                messages=[],
                max_iterations=await self._read_max_iterations(),
                a2a_kind=(source if is_a2a else None),
                a2a_source_magi_id=(getattr(job, "source_magi_id", None) if is_a2a else None),
                a2a_job_id=(job.job_id if is_a2a else None),
                a2a_inbound_text=a2a_inbound_text,
            )
            try:
                self._managed_contexts.add(id(ctx))
                await self._process(ctx)
            except asyncio.CancelledError:
                ctx.failed = True
                raise
            except Exception as exc:
                logger.exception("agent run failed conv=%s", conversation_id)
                ctx.failed = True
                ctx.final_reply = ctx.final_reply or "抱歉，处理请求时发生了错误。"
                await self._publish_delivery(ctx)
            finally:
                succeeded = not ctx.failed
                if source == "chat":
                    chat_job_id = job.job_id
                    await self.call(
                        self.bus.agent_job_board.submit_result,
                        job_id=chat_job_id,
                        worker_id=self.worker_id,
                        result=ChatNotifyResult(
                            job_id=chat_job_id,
                            status=JobStatus.COMPLETED if succeeded else JobStatus.FAILED,
                        ),
                    )
                elif source == "a2a.request":
                    from old_bus.firmwares.jobs.a2aJob import A2ARequestResult

                    board = self.bus.a2a_request_job_board
                    if board is not None:
                        a2a_request_job_id = job.job_id
                        await self.call(
                            board.submit_result,
                            job_id=a2a_request_job_id,
                            worker_id=self.worker_id,
                            result=A2ARequestResult(
                                job_id=a2a_request_job_id,
                                status=JobStatus.COMPLETED if succeeded else JobStatus.FAILED,
                                content=ctx.final_reply,
                            ),
                        )
                elif source == "a2a.notify":
                    from old_bus.firmwares.jobs.a2aJob import A2ANotifyResult

                    board = self.bus.a2a_notify_job_board
                    if board is not None:
                        a2a_notify_job_id = job.job_id
                        await self.call(
                            board.submit_result,
                            job_id=a2a_notify_job_id,
                            worker_id=self.worker_id,
                            result=A2ANotifyResult(
                                job_id=a2a_notify_job_id,
                                status=JobStatus.COMPLETED if succeeded else JobStatus.FAILED,
                            ),
                        )
                current_event = self._in_flight.get(ctx.conversation_id)
                if current_event is ctx.cancel_event:
                    self._in_flight.pop(ctx.conversation_id, None)
                if ctx.a2a_source_magi_id is not None:
                    self._active_a2a_peers.discard(ctx.a2a_source_magi_id)
                self._managed_contexts.discard(id(ctx))

    async def _claim_next_turn(self) -> tuple[str | None, Any | None]:
        async with self._claim_lock:
            return await self._claim_next_turn_locked()

    async def _claim_next_turn_locked(self) -> tuple[str | None, Any | None]:
        """Fairly claim local chat and MAGIS-shared A2A work."""
        agent_board = self.bus.agent_job_board

        def claim_chat():
            return agent_board.claim_for_new_conversation(
                worker_id=self.worker_id,
                active_conversation_ids=set(self._in_flight),
            )

        choices: list[tuple[str, Any]] = [("chat", claim_chat)]
        # Bind to locals so Pylance keeps the ``is not None``
        # narrowing across the lambda boundary; ``self.bus.*`` and
        # ``self._magi_id`` would otherwise be re-typed as
        # ``T | None`` inside the closure and trigger
        # ``reportOptionalMemberAccess`` / ``reportArgumentType``.
        magi_id = self._magi_id
        request_board = self.bus.a2a_request_job_board
        notify_board = self.bus.a2a_notify_job_board
        if magi_id is not None and request_board is not None:
            choices.append(
                (
                    "a2a.request",
                    lambda: request_board.claim_for_target(
                        magi_id=magi_id,
                        worker_id=self.worker_id,
                        active_source_magi_ids=set(self._active_a2a_peers),
                    ),
                )
            )
        if magi_id is not None and notify_board is not None:
            choices.append(
                (
                    "a2a.notify",
                    lambda: notify_board.claim_for_target(
                        magi_id=magi_id,
                        worker_id=self.worker_id,
                        active_source_magi_ids=set(self._active_a2a_peers),
                    ),
                )
            )
        offset = self._claim_cursor % len(choices)
        self._claim_cursor += 1
        for index in range(len(choices)):
            source, claim = choices[(offset + index) % len(choices)]
            job = await self.call(claim)
            if job is not None:
                if source == "chat":
                    conversation_id = getattr(job, "conversation_id", None) or 0
                    if conversation_id:
                        # Reserve the key before dropping ``_claim_lock``.
                        # ``_process`` replaces this placeholder with the
                        # context's real cancellation event.
                        self._in_flight[conversation_id] = asyncio.Event()
                else:
                    peer_magi_id = getattr(job, "source_magi_id", None)
                    if isinstance(peer_magi_id, int):
                        self._active_a2a_peers.add(peer_magi_id)
                return source, job
        return None, None

    # -- agent loop ----------------------------------------------------------

    async def _process(self, ctx: RunContext) -> None:
        await self._load_history(ctx)
        self._in_flight[ctx.conversation_id] = ctx.cancel_event
        try:
            for _ in range(ctx.max_iterations):
                if ctx.cancel_event.is_set():
                    ctx.cancelled = True
                    ctx.final_reply = "任务已取消。"
                    await self._publish_delivery(ctx)
                    return

                llm_job = await self._build_llm_job(ctx)
                if llm_job is None:
                    # Defensive only — ``_build_llm_job`` always returns a
                    # :class:`CallLLMJob` today. If it ever returns ``None``
                    # it's an internal agent failure with no upstream to
                    # forward; surface a short constant.
                    ctx.final_reply = "内部错误：无法构建 LLM 请求"
                    ctx.failed = True
                    await self._publish_delivery(ctx)
                    return

                llm_job_id = await self.call(self.bus.llm_job_board.publish, llm_job)
                result = await self._wait_for_llm(llm_job_id)

                if ctx.cancel_event.is_set():
                    ctx.cancelled = True
                    ctx.final_reply = "任务已取消。"
                    await self._publish_delivery(ctx)
                    return

                if result is None:
                    ctx.final_reply = "抱歉，回复生成超时，请稍后再试。"
                    ctx.failed = True
                    await self._publish_delivery(ctx)
                    return
                if result.status != JobStatus.COMPLETED:
                    # Forward the upstream ``error`` verbatim. The provider
                    # worker already ships a human-readable failure string;
                    # rephrasing it here would lose fidelity.
                    ctx.final_reply = _format_llm_error(result)
                    ctx.failed = True
                    await self._publish_delivery(ctx)
                    return

                assistant_msg = self._build_assistant_message(result)
                ctx.messages.append(assistant_msg)

                resp = getattr(result, "response", None) or {}
                text: str = resp.get("text") or ""
                tool_uses: list[dict] = resp.get("tool_uses") or []

                if not tool_uses:
                    ctx.final_reply = text
                    await self._publish_delivery(ctx)
                    self._maybe_title(ctx)
                    return

                split = await self._split_tools(ctx, tool_uses)
                tool_jobs_by_call, a2a_requests_by_call, a2a_notify_results = await self._publish_effects(split)
                gather = await self._gather_all(
                    ctx,
                    tool_jobs_by_call,
                    a2a_requests_by_call,
                    a2a_notify_results,
                )
                if gather is None:
                    ctx.failed = True
                    return

                self._append_tool_result_user_message(ctx, gather)

            ctx.final_reply = "已达到最大工具调用次数，请简化你的请求。"
            await self._publish_delivery(ctx)
        finally:
            if (
                id(ctx) not in self._managed_contexts
                and self._in_flight.get(ctx.conversation_id) is ctx.cancel_event
            ):
                self._in_flight.pop(ctx.conversation_id, None)

    # -- context assembly ----------------------------------------------------

    async def _load_history(self, ctx: RunContext) -> None:
        if ctx.a2a_kind is not None:
            # A2A transcripts are system-owned (no Contact row), so the
            # contact-scoped chat helper cannot read them.  Claim has already
            # persisted the current inbound text as a user row in this exact
            # local conversation; reload the complete peer thread so later
            # turns remember both directions.
            if not ctx.conversation_id:
                ctx.messages = [{"role": "user", "content": ctx.a2a_inbound_text}]
                return
            try:
                dtos = await self.call(
                    self.bus.messages_book.list_for_conversation,
                    conversation_id=ctx.conversation_id,
                )
                ctx.messages = [
                    {
                        "role": "user" if message.role in ("user", "system") else "assistant",
                        "content": message.text,
                    }
                    for message in dtos
                ]
            except Exception:
                logger.warning("load A2A transcript failed, using current turn", exc_info=True)
                ctx.messages = []

            # A Board transcript write is deliberately best-effort.  If it
            # failed after a successful claim, retain the original one-turn
            # behavior instead of sending an empty A2A prompt to the LLM.
            if not ctx.messages or ctx.messages[-1]["content"] != ctx.a2a_inbound_text:
                ctx.messages.append({"role": "user", "content": ctx.a2a_inbound_text})
            return

        if ctx.messages:
            return
        if not ctx.conversation_id or ctx.contact_id is None:
            return
        from agent.agent_context import build_messages_from_conversation

        try:
            msgs = await self.call(
                build_messages_from_conversation,
                contact_id=ctx.contact_id,
                conversation_id=ctx.conversation_id,
                new_user_text="",
                bus=self.bus,
            )
            ctx.messages = list(msgs)  # already list[dict]
        except Exception:
            logger.warning("load_history failed, starting fresh", exc_info=True)
            return

        # Auto-compaction: if history (with summary) crosses the
        # threshold, fold old messages into the cumulative summary,
        # archive them, and replace ctx.messages with the new dict list.
        # Awaited (not fire-and-forget) because the result feeds back
        # into ctx.messages. Compaction is rare so the await cost is OK.
        try:
            from agent.compaction import maybe_compact

            dtos = self.bus.messages_book.list_for_conversation(
                conversation_id=ctx.conversation_id, include_archived=False
            )
            # ``maybe_compact`` is ``async def``; ``call`` (which uses
            # ``asyncio.to_thread``) only handles sync callables — using it
            # here would return a coroutine object instead of the awaited
            # value, breaking the ``is not None`` narrow below.
            compacted = await maybe_compact(
                contact_id=ctx.contact_id,
                conversation_id=ctx.conversation_id,
                message_dtos=dtos,
                bus=self.bus,
            )
            if compacted is not None:
                ctx.messages = compacted
        except Exception:
            logger.warning("maybe_compact failed, continuing with loaded history", exc_info=True)

    async def _build_llm_job(self, ctx: RunContext) -> Any:
        """组装完整 LLM 请求。不检查 provider 配置——ProvidersWorker 自己处理。"""
        from old_bus.firmwares.jobs.callLLMJob import CallLLMJob

        system = await self._system_prompt(ctx)
        messages = [{"role": "system", "content": system}] + list(ctx.messages)
        tools = await self._tool_schemas(ctx.contact_id)

        return CallLLMJob(
            messages=messages,
            contact_id=ctx.contact_id,
            max_tokens=await self._read_max_tokens(),
            tools=tools or None,
            streaming=False,
        )

    async def _system_prompt(self, ctx: RunContext) -> str:
        from agent.system_prompt import build_system_prompt, read_soul

        try:
            system = await self.call(
                lambda: build_system_prompt(
                    contact_id=ctx.contact_id or 0,
                    soul=read_soul(bus=self.bus),
                    bus=self.bus,
                    magi_id=self._magi_id,
                )
            )
            if ctx.a2a_kind == "a2a.notify":
                return (
                    system
                    + "\n\n## Current A2A notification\n"
                    "This is a one-way peer notification. Do not automatically reply to it. "
                    "If collaboration is truly needed, explicitly call message_magi to create a new message."
                )
            if ctx.a2a_kind == "a2a.request":
                return (
                    system
                    + "\n\n## Current A2A request\n"
                    "Provide one direct final answer to this request. The runtime will return that answer "
                    "to the requester exactly once; do not create an automatic follow-up reply."
                )
            return system
        except Exception:
            logger.exception("system_prompt build failed; falling back to bare soul")
            return "You are a helpful assistant."

    async def _tool_schemas(self, contact_id: int | None) -> list[dict] | None:
        try:
            # Resolve the caller's role live from the Contact row rather
            # than trusting a publish-time snapshot — a demoted operator
            # must immediately lose access to elevated tools.
            caller_role: str | None = None
            if contact_id is not None:
                contact = await self.call(self.bus.contacts_book.get, contact_id=contact_id)
                caller_role = contact.role if contact else None
            defs = await self.call(
                self.bus.tool_definitions_book.list_enabled,
                caller_role=caller_role,
            )
            result = []
            for d in defs or []:
                result.append(
                    {
                        "name": getattr(d, "name", ""),
                        "description": getattr(d, "description", ""),
                        "input_schema": getattr(d, "input_schema", {}),
                    }
                )
            return result if result else None
        except Exception:
            logger.warning("tool schemas load failed", exc_info=True)
            return None

    # -- LLM wait ------------------------------------------------------------

    async def _wait_for_llm(self, llm_job_id: int) -> CallLLMResult | None:
        # ``get_result`` is a one-shot query that returns ``None`` the
        # instant the result row does not exist yet — wrapping it in
        # ``asyncio.wait_for`` only enforces an upper bound, so the
        # call returned ``None`` long before the providers worker
        # could poll (0.25s cadence) and complete the call. The board
        # exposes ``wait_for_result`` which polls every 50ms; use it
        # so the agent actually waits for the LLM result instead of
        # declaring a spurious timeout.
        timeout = await self._read_llm_timeout()
        try:
            result = await self.bus.llm_job_board.wait_for_result(
                job_id=llm_job_id,
                timeout=timeout,
                poll_interval=0.05,
            )
        except Exception:
            logger.exception("llm wait crashed for %s", llm_job_id)
            return None
        if result is None:
            logger.warning("llm job %s timed out (%.0fs)", llm_job_id, timeout)
        return result

    # -- split tools ---------------------------------------------------------

    @staticmethod
    def _make_tool_job(
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        context: dict,
    ) -> RunToolJob:
        from old_bus.firmwares.jobs.runToolJob import RunToolJob

        return RunToolJob(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload={"arguments": arguments, "context": context},
        )

    async def _split_tools(self, ctx: RunContext, tool_uses: list[dict]) -> _SplitJobs:
        from old_bus.firmwares.jobs.a2aJob import A2ANotifyJob, A2ARequestJob

        tool_jobs: list[RunToolJob] = []
        a2a_request_jobs: list[tuple[str, A2ARequestJob]] = []
        a2a_notify_jobs: list[tuple[str, A2ANotifyJob]] = []

        for tu in tool_uses:
            name = tu.get("name", "")
            args = dict(tu.get("input") or {})
            tool_call_id = str(tu.get("id") or uuid.uuid4().hex)
            context = {
                "workspace": "",
                "contact_id": ctx.contact_id or 0,
                "channel": ctx.channel,
                "conversation_id": ctx.conversation_id or 0,
            }
            if name == "message_magi":
                if (
                    not _A2A_ENABLED
                    or self._magi_id is None
                    or self.bus.a2a_request_job_board is None
                    or self.bus.a2a_notify_job_board is None
                ):
                    tool_jobs.append(
                        self._make_tool_job(
                            tool_call_id,
                            "message_magi",
                            {"_validation_error": "a2a_disabled"},
                            context,
                        )
                    )
                    continue
                try:
                    target_magi_id = int(args["magi_id"])
                    text = str(args["text"])
                    mode = str(args["mode"]).strip().lower()
                    if target_magi_id <= 0 or not text.strip() or mode not in {"notify", "request"}:
                        raise ValueError("magi_id, text, and mode=notify|request required")
                    deadline_seconds = int(args.get("deadline_seconds") or 120)
                    if not 1 <= deadline_seconds <= 3600:
                        raise ValueError("deadline_seconds must be between 1 and 3600")
                except (KeyError, TypeError, ValueError) as exc:
                    tool_jobs.append(
                        self._make_tool_job(
                            tool_call_id,
                            "message_magi",
                            {"_validation_error": str(exc)},
                            context,
                        )
                    )
                    continue
                if mode == "request":
                    a2a_request_jobs.append(
                        (
                            tool_call_id,
                            A2ARequestJob(
                                source_magi_id=self._magi_id,
                                target_magi_id=target_magi_id,
                                text=text,
                                deadline_at=(
                                    datetime.now(UTC).replace(tzinfo=None)
                                    + timedelta(seconds=deadline_seconds)
                                ),
                            ),
                        )
                    )
                else:
                    a2a_notify_jobs.append(
                        (
                            tool_call_id,
                            A2ANotifyJob(
                                source_magi_id=self._magi_id,
                                target_magi_id=target_magi_id,
                                text=text,
                            ),
                        )
                    )
            else:
                tool_jobs.append(
                    self._make_tool_job(
                        tool_call_id,
                        name or "",
                        args,
                        context,
                    )
                )
        return _SplitJobs(
            tool_jobs=tool_jobs,
            a2a_request_jobs=a2a_request_jobs,
            a2a_notify_jobs=a2a_notify_jobs,
        )

    # -- publish effects -----------------------------------------------------

    async def _publish_effects(
        self, split: _SplitJobs
    ) -> tuple[dict[str, int], dict[str, int], dict[str, Any]]:
        """Publish effects and return tool / request mappings plus immediate notify results.

        Each returned dict is keyed by ``tool_call_id`` (the LLM
        ``tool_use.id`` shared by the originating tool call); the int
        values are the corresponding board's ``job_id`` (``tool_job_id``
        for the tool board, ``a2a_request_job_id`` for the request board).
        ``a2a_notify_results`` keys the same way but holds the synchronously
        recorded {success, content} dict because the notify board does not
        return a per-call job_id.
        """
        tool_jobs_by_call: dict[str, int] = {}
        a2a_requests_by_call: dict[str, int] = {}
        a2a_notify_results: dict[str, Any] = {}
        for tj in split.tool_jobs:
            tool_job_id = await self.call(self.bus.tool_job_board.publish, tj)
            tool_jobs_by_call[tj.tool_call_id] = tool_job_id
        if self.bus.a2a_request_job_board is not None:
            for tool_call_id, request in split.a2a_request_jobs:
                try:
                    a2a_request_job_id = await self.call(
                        self.bus.a2a_request_job_board.publish, request
                    )
                    a2a_requests_by_call[tool_call_id] = a2a_request_job_id
                except (LookupError, ValueError) as exc:
                    a2a_notify_results[tool_call_id] = {
                        "success": False,
                        "content": f"A2A request was rejected: {exc}",
                    }
        if self.bus.a2a_notify_job_board is not None:
            for tool_call_id, notify in split.a2a_notify_jobs:
                try:
                    await self.call(self.bus.a2a_notify_job_board.publish, notify)
                    a2a_notify_results[tool_call_id] = {
                        "success": True,
                        "content": "A2A notification persisted for the target MAGI.",
                    }
                except (LookupError, ValueError) as exc:
                    a2a_notify_results[tool_call_id] = {
                        "success": False,
                        "content": f"A2A notification was rejected: {exc}",
                    }
        return tool_jobs_by_call, a2a_requests_by_call, a2a_notify_results

    # -- gather results + steering -------------------------------------------

    async def _gather_all(
        self,
        ctx: RunContext,
        tool_jobs_by_call: dict[str, int],  # tool_call_id → tool_job_id
        a2a_requests_by_call: dict[str, int],  # tool_call_id → a2a_request_job_id
        a2a_notify_results: dict[str, Any],
    ) -> _GatherResult | None:
        deadline = asyncio.get_running_loop().time() + await self._read_tool_wait()
        pending_tool_jobs: dict[str, int] = dict(tool_jobs_by_call)  # tool_call_id → tool_job_id (copy to mutate)
        pending_a2a_jobs: dict[str, int] = dict(a2a_requests_by_call)
        tool_results: dict[str, Any] = {}
        a2a_results: dict[str, Any] = dict(a2a_notify_results)
        steering_parts: list[str] = []

        while pending_tool_jobs or pending_a2a_jobs:
            if ctx.cancel_event.is_set():
                break

            # steering
            if ctx.conversation_id and len(steering_parts) < _MAX_STEERING_PARTS:
                steer = await self.call(
                    self.bus.agent_job_board.claim_for_steering,
                    conversation_id=ctx.conversation_id,
                    worker_id=self.worker_id,
                )
                if steer is not None:
                    text = steer.text or ""
                    if text:
                        steering_parts.append(text)
                    from old_bus.firmwares.jobs.chatNotifyJob import ChatNotifyResult

                    chat_steer_job_id = steer.job_id
                    await self.call(
                        self.bus.agent_job_board.submit_result,
                        job_id=chat_steer_job_id,
                        worker_id=self.worker_id,
                        result=ChatNotifyResult(
                            job_id=chat_steer_job_id,
                            status=JobStatus.COMPLETED,
                        ),
                    )

            # tool results
            for tool_call_id, tool_job_id in list(pending_tool_jobs.items()):
                r = await self.call(self.bus.tool_job_board.get_result, job_id=tool_job_id)
                if r is not None:
                    tool_results[tool_call_id] = r
                    del pending_tool_jobs[tool_call_id]

            # a2a results
            for tool_call_id, a2a_request_job_id in list(pending_a2a_jobs.items()):
                board = self.bus.a2a_request_job_board
                r = await self.call(board.get_result, job_id=a2a_request_job_id) if board is not None else None
                if r is not None:
                    a2a_results[tool_call_id] = r
                    del pending_a2a_jobs[tool_call_id]

            if not pending_tool_jobs and not pending_a2a_jobs:
                break
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning(
                    "gather timeout, pending_tools=%d pending_a2a=%d",
                    len(pending_tool_jobs),
                    len(pending_a2a_jobs),
                )
                break
            await asyncio.sleep(0.1)

        from old_bus.firmwares.jobs.runToolJob import RunToolResult, ToolErrorCode

        for tool_call_id, tool_job_id in pending_tool_jobs.items():
            tool_results[tool_call_id] = RunToolResult(
                job_id=tool_job_id,
                status=JobStatus.FAILED,
                content="tool execution timed out",
                error_code=ToolErrorCode.FAILED,
                tool_call_id=tool_call_id,
            )

        from old_bus.firmwares.jobs.a2aJob import A2AErrorCode, A2ARequestResult

        for tool_call_id, a2a_request_job_id in pending_a2a_jobs.items():
            a2a_results[tool_call_id] = A2ARequestResult(
                job_id=a2a_request_job_id,
                status=JobStatus.FAILED,
                error_code=A2AErrorCode.TIMEOUT,
                error="A2A request timed out",
            )

        steering_text = "\n\n".join(steering_parts) if steering_parts else None
        return _GatherResult(
            tool_results=tool_results,
            a2a_results=a2a_results,
            steering_text=steering_text,
        )

    # -- output --------------------------------------------------------------

    def _append_tool_result_user_message(self, ctx: RunContext, gather: _GatherResult) -> None:
        from old_bus.firmwares.jobs.runToolJob import ToolErrorCode

        blocks: list[dict] = []
        for tool_call_id, r in gather.tool_results.items():
            blocks.append(
                {
                    "tool_use_id": tool_call_id,
                    "type": "tool_result",
                    "content": getattr(r, "content", "") or "",
                    "is_error": getattr(r, "error_code", ToolErrorCode.NONE) != ToolErrorCode.NONE,
                }
            )
        for tool_call_id, r in gather.a2a_results.items():
            if isinstance(r, dict):
                content = str(r.get("content") or "")
                success = bool(r.get("success", False))
            else:
                content = getattr(r, "content", "") or ""
                success = bool(getattr(r, "success", False))
            blocks.append(
                {
                    "tool_use_id": tool_call_id,
                    "type": "tool_result",
                    "content": content,
                    "is_error": not success,
                }
            )
        if gather.steering_text:
            blocks.append({"type": "text", "text": gather.steering_text})
        ctx.messages.append(
            {
                "role": "user",
                "content": gather.steering_text or "",
                "content_blocks": blocks,
            }
        )

    async def _publish_delivery(self, ctx: RunContext) -> None:
        from old_bus.firmwares.jobs.deliveryNotifyJob import DeliveryNotifyJob

        # A2A has its own terminal path in ``_run``.  Its text is either the
        # single request response or deliberately discarded for a notify.
        if ctx.a2a_kind is not None:
            return

        # Resolve the reply address from the conversation row (D.28): the
        # delivery worker needs ``destination`` for address-based channels
        # (TG chat id). WebUI appends by ``conversation_id`` and ignores it.
        destination: str | None = None
        if ctx.contact_id is not None:
            conversation = await self.call(
                self.bus.conversations_book.get_for_owner,
                contact_id=ctx.contact_id,
                conversation_id=ctx.conversation_id,
            )
            if conversation is not None:
                destination = getattr(conversation, "delivery_address", None) or None

        await self.call(
            self.bus.delivery_notify_job_board.publish,
            DeliveryNotifyJob(
                channel=ctx.channel,
                text=ctx.final_reply or "处理完毕。",
                conversation_id=ctx.conversation_id,
                contact_id=ctx.contact_id,
                destination=destination,
            ),
        )

    def _maybe_title(self, ctx: RunContext) -> None:
        if not ctx.conversation_id or ctx.contact_id is None:
            return
        from agent.auto_title import request_conversation_title

        self.spawn(
            request_conversation_title(ctx.contact_id, ctx.conversation_id, bus=self.bus),
            name=f"magi-title-{ctx.conversation_id}",
        )

    # -- helpers -------------------------------------------------------------

    def _build_assistant_message(self, result: CallLLMResult) -> dict:
        resp = getattr(result, "response", None) or {}
        msg = {"role": "assistant", "content": resp.get("text") or ""}
        blocks = resp.get("raw_blocks")
        if blocks:
            msg["content_blocks"] = blocks
        return msg

    # -- cancel --------------------------------------------------------------

    def _broadcast_cancel(self, conversation_id: int) -> None:
        event = self._in_flight.get(conversation_id)
        if event is not None:
            event.set()

    # -- settings helpers ----------------------------------------------------

    async def _read_max_iterations(self) -> int:
        raw = await self.call(self.bus.settings_book.get_value, key="agent.max_iterations")
        return _coerce_int(raw, _DEFAULT_MAX_ITERATIONS)

    async def _read_max_tokens(self) -> int:
        raw = await self.call(self.bus.settings_book.get_value, key="agent.max_tokens")
        return _coerce_int(raw, _DEFAULT_MAX_TOKENS)

    async def _read_tool_wait(self) -> float:
        raw = await self.call(self.bus.settings_book.get_value, key="agent.tool_wait_seconds")
        return _coerce_float(raw, _DEFAULT_TOOL_WAIT_SECONDS)

    async def _read_llm_timeout(self) -> float:
        raw = await self.call(self.bus.settings_book.get_value, key="agent.llm_timeout_seconds")
        return _coerce_float(raw, _DEFAULT_LLM_TIMEOUT_SECONDS)


async def submit_agent_message(bus: Bus, message: Any) -> int:
    from old_bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob

    job = ChatNotifyJob(
        # ``job_id`` is database-owned and is filled after publish().
        conversation_id=getattr(message, "conversation_id", None) or 0,
        text=getattr(message, "text", ""),
        channel=getattr(message, "channel", ""),
        contact_id=getattr(message, "contact_id", None),
    )
    return await asyncio.to_thread(bus.agent_job_board.publish, job)


# ---------------------------------------------------------------------------
# private helpers
# ---------------------------------------------------------------------------


def _coerce_int(raw: Any, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _coerce_float(raw: Any, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _format_llm_error(result: Any) -> str:
    """Return the upstream failure text from a failed :class:`CallLLMResult`.

    The provider worker already ships a human-readable ``error`` string;
    the agent forwards it verbatim. A constant placeholder is used only
    when that field is empty.
    """
    error = getattr(result, "error", None) or ""
    return error or "回复生成失败"
