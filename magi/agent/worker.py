"""AgentWorker — bus 上唯一的 agent turn consumer.

设计原则（与 :class:`~magi.tools.worker.ToolsWorker` 、
:class:`~magi.providers.worker.ProvidersWorker` 对齐）：

- **只依赖 bus**。老的 ``magi.bus`` store / facade 一概不碰。
- **构造靠注入**。``AgentWorker(bus: Bus)`` 由 composition root 显式注入。
- **board claim steering**：steering 不通过进程内队列，而是在
  ``_gather_all`` 中主动 ``claim_for_steering`` 认领同 session 的新
  ChatNotifyJob。board 本身是唯一持久化协调点。
- **回复走 delivery_job_board**：``ChatNotifyResult`` 只承载 success/error_code，
  回复文本统一由 ``_publish_delivery`` 投递。

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

from magi.bus.guild.base import JobStatus
from magi.runtime_worker import RuntimeWorker

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.callLLMJob import CallLLMResult
    from magi.bus.guild.chatNotifyJob import ChatErrorCode
    from magi.bus.guild.runToolJob import RunToolJob

logger = logging.getLogger("magi.agent.worker")

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
    conversation_id: str
    channel: str
    messages: list[dict] = field(default_factory=list)
    max_iterations: int = _DEFAULT_MAX_ITERATIONS
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    final_reply: str = ""
    final_error: ChatErrorCode | None = None  # 稳定错误码（成功为 None）
    final_error_detail: str | None = None  # 失败详情（底层码 + 人类文案）
    cancelled: bool = False
    a2a_kind: str | None = None
    a2a_source_magi_id: int | None = None
    a2a_job_id: str | None = None


def _llm_failure_detail(result: "CallLLMResult") -> str | None:
    """拼 LLM 失败的人类可读文案（写入继承的 ``BaseJobResult.error``）：底层稳定码 + 人类文案。

    底层码保留 :class:`~magi.bus.guild.callLLMJob.LLMErrorCode` 的
    ``.value``（如 ``llm.auth_failed``），文案保留 provider 给的
    ``error``。至少一项非空才返回，否则 ``None``。
    """
    code = getattr(result, "error_code", None)
    text = getattr(result, "error", None)
    parts: list[str] = []
    if code is not None and str(code) != "":
        parts.append(str(code))
    if text:
        parts.append(str(text))
    return " | ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _SplitJobs:
    # ``tool_jobs`` carries :class:`RunToolJob` (which keeps its own
    # ``tool_call_id``); A2A jobs no longer carry ``tool_call_id`` on the
    # wire, so we pair each one with the LLM tool_call_id it came from
    # here — downstream ``_publish_effects`` / ``_gather_all`` both want
    # ``tc_id`` as the dict key.
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
    """Sequential consumer of one MAGI's ``chat_notify_jobs`` stream."""

    worker_name = "agent"

    def __init__(self, bus: Bus, *, poll_seconds: float = 0.25, magi_id: int | None = None) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self.worker_id = f"agent-{uuid.uuid4().hex}"
        self._active_conversations: set[str] = set()
        self._in_flight: dict[str, asyncio.Event] = {}  # conversation_id → cancel_event
        # ``magi_id`` is the runtime's own ``magis_memberships.id`` —
        # propagated in from :class:`WorkerRegistry`, which reads it
        # from the provisioned RuntimeSpec at boot
        # (:mod:`magi.startup.runtime`). Used by
        # :meth:`_system_prompt` to render the per-MAGI instruction
        # block (team + role layers from MAGIS Books); ``None``
        # short-circuits that lookup and renders only the personal
        # instruction.
        self._magi_id = magi_id
        self._claim_cursor = 0

    # -- main loop -----------------------------------------------------------

    async def _run(self) -> None:
        from magi.bus.guild.chatNotifyJob import ChatErrorCode, ChatNotifyResult

        while not self._stopping:
            source, job = await self._claim_next_turn()
            if job is None or source is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            conversation_id = getattr(job, "conversation_id", None) or ""

            # steering — release back to board for _process to claim
            if (
                source == "chat"
                and conversation_id
                and conversation_id in self._active_conversations
            ):
                await self.call(self.bus.agent_job_board.release, key=job.job_id)
                continue

            # new run
            if source == "chat":
                self._active_conversations.add(conversation_id)
            is_a2a = source != "chat"
            if is_a2a:
                # A2A jobs carry no conversation_id on the wire: the
                # conversation is the single per-peer thread, keyed by the
                # source MAGI's membership id.
                conversation_id = f"a2a:{getattr(job, 'source_magi_id', 0)}"
            ctx = RunContext(
                contact_id=(None if is_a2a else job.contact_id),
                conversation_id=(conversation_id or f"{source}:{job.job_id}"),
                channel=(source if is_a2a else job.channel),
                messages=(
                    [{"role": "user", "content": getattr(job, "text", "")}]
                    if is_a2a
                    else []
                ),
                max_iterations=await self._read_max_iterations(),
                a2a_kind=(source if is_a2a else None),
                a2a_source_magi_id=(getattr(job, "source_magi_id", None) if is_a2a else None),
                a2a_job_id=(job.job_id if is_a2a else None),
            )
            try:
                await self._process(ctx)
            except asyncio.CancelledError:
                ctx.final_error = ChatErrorCode.RUN_CANCELLED
                raise
            except Exception as exc:
                logger.exception("agent run failed conv=%s", conversation_id)
                ctx.final_error = ChatErrorCode.AGENT_CRASHED
                ctx.final_error_detail = str(exc)
                ctx.final_reply = ctx.final_reply or "抱歉，处理请求时发生了错误。"
                await self._publish_delivery(ctx)
            finally:
                if source == "chat" and conversation_id:
                    self._active_conversations.discard(conversation_id)
                succeeded = ctx.final_error is None
                if source == "chat":
                    await self.call(
                        self.bus.agent_job_board.submit_result,
                        key=job.job_id,
                        result=ChatNotifyResult(
                            job_id=job.job_id,
                            status=JobStatus.COMPLETED if succeeded else JobStatus.FAILED,
                            error_code=ctx.final_error,
                            error=ctx.final_error_detail,
                        ),
                    )
                elif source == "a2a.request":
                    from magi.bus.guild.a2aJob import A2AErrorCode, A2ARequestResult

                    board = self.bus.a2a_request_job_board
                    if board is not None:
                        # ``error_code`` only carries A2A-board-managed
                        # codes (``A2AErrorCode``); the worker's own
                        # business codes (``magi.run_cancelled`` /
                        # ``agent_crashed`` / ``llm_timeout`` / …) flow
                        # through ``error`` so consumers still see them
                        # without breaking the strict StrEnum contract.
                        board_code = (
                            A2AErrorCode.TIMEOUT
                            if ctx.final_error == A2AErrorCode.TIMEOUT.value
                            else None
                        )
                        await self.call(
                            board.submit_result,
                            key=job.job_id,
                            result=A2ARequestResult(
                                job_id=job.job_id,
                                status=JobStatus.COMPLETED if succeeded else JobStatus.FAILED,
                                content=ctx.final_reply,
                                error_code=board_code,
                                error=ctx.final_error,
                            ),
                        )
                elif source == "a2a.notify":
                    from magi.bus.guild.a2aJob import A2ANotifyResult

                    board = self.bus.a2a_notify_job_board
                    if board is not None:
                        # Notify has no board-managed codes yet — the
                        # worker's business code flows through ``error``
                        # and ``error_code`` stays ``None`` so the
                        # StrEnum contract isn't violated.
                        await self.call(
                            board.submit_result,
                            key=job.job_id,
                            result=A2ANotifyResult(
                                job_id=job.job_id,
                                status=JobStatus.COMPLETED if succeeded else JobStatus.FAILED,
                                error_code=None,
                                error=ctx.final_error,
                            ),
                        )

    async def _claim_next_turn(self) -> tuple[str | None, Any | None]:
        """Fairly claim local chat and MAGIS-shared A2A work."""
        choices: list[tuple[str, Any]] = [("chat", self.bus.agent_job_board.claim)]
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
                    lambda: request_board.claim_for_target(magi_id=magi_id),
                )
            )
        if magi_id is not None and notify_board is not None:
            choices.append(
                (
                    "a2a.notify",
                    lambda: notify_board.claim_for_target(magi_id=magi_id),
                )
            )
        offset = self._claim_cursor % len(choices)
        self._claim_cursor += 1
        for index in range(len(choices)):
            source, claim = choices[(offset + index) % len(choices)]
            job = await self.call(claim)
            if job is not None:
                return source, job
        return None, None

    # -- agent loop ----------------------------------------------------------

    async def _process(self, ctx: RunContext) -> None:
        from magi.bus.guild.chatNotifyJob import ChatErrorCode

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
                    ctx.final_reply = await self._fallback_reply()
                    await self._publish_delivery(ctx)
                    return

                job_id = await self.call(self.bus.llm_job_board.publish, llm_job)
                result = await self._wait_for_llm(job_id)

                if ctx.cancel_event.is_set():
                    ctx.cancelled = True
                    ctx.final_reply = "任务已取消。"
                    await self._publish_delivery(ctx)
                    return

                if result is None:
                    ctx.final_reply = "抱歉，回复生成超时，请稍后再试。"
                    ctx.final_error = ChatErrorCode.LLM_TIMEOUT
                    await self._publish_delivery(ctx)
                    return
                if result.status != JobStatus.COMPLETED:
                    ctx.final_reply = "抱歉，回复生成失败，请稍后再试。"
                    ctx.final_error = ChatErrorCode.LLM_FAILED
                    ctx.final_error_detail = _llm_failure_detail(result)
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
                tool_ids, request_ids, notify_results = await self._publish_effects(split)
                gather = await self._gather_all(
                    ctx,
                    tool_ids,
                    request_ids,
                    notify_results,
                )
                if gather is None:
                    ctx.final_error = ChatErrorCode.LEASE_LOST
                    return

                self._append_tool_result_user_message(ctx, gather)

            ctx.final_reply = "已达到最大工具调用次数，请简化你的请求。"
            await self._publish_delivery(ctx)
        finally:
            self._in_flight.pop(ctx.conversation_id, None)

    # -- context assembly ----------------------------------------------------

    async def _load_history(self, ctx: RunContext) -> None:
        if ctx.messages:
            return
        if not ctx.conversation_id or ctx.contact_id is None:
            return
        from magi.agent.agent_context import build_messages_from_conversation

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
            from magi.agent.compaction import maybe_compact

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
        from magi.bus.guild.callLLMJob import CallLLMJob

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
        from magi.agent.system_prompt import build_system_prompt, read_soul

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

    async def _wait_for_llm(self, job_id: str) -> CallLLMResult | None:
        timeout = await self._read_llm_timeout()
        try:
            return await asyncio.wait_for(
                self.call(self.bus.llm_job_board.get_result, key=job_id),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("llm job %s timed out (%.0fs)", job_id, timeout)
            return None

    # -- split tools ---------------------------------------------------------

    @staticmethod
    def _make_tool_job(
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        context: dict,
    ) -> RunToolJob:
        from magi.bus.guild.runToolJob import RunToolJob

        return RunToolJob(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload={"arguments": arguments, "context": context},
        )

    async def _split_tools(self, ctx: RunContext, tool_uses: list[dict]) -> _SplitJobs:
        from magi.bus.guild.a2aJob import A2ANotifyJob, A2ARequestJob

        tool_jobs: list[RunToolJob] = []
        a2a_request_jobs: list[tuple[str, A2ARequestJob]] = []
        a2a_notify_jobs: list[tuple[str, A2ANotifyJob]] = []

        for tu in tool_uses:
            name = tu.get("name", "")
            args = dict(tu.get("input") or {})
            tc_id = str(tu.get("id") or uuid.uuid4().hex)
            context = {
                "workspace": "",
                "contact_id": ctx.contact_id or 0,
                "channel": ctx.channel,
                "conversation_id": ctx.conversation_id or "",
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
                            tc_id,
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
                            tc_id,
                            "message_magi",
                            {"_validation_error": str(exc)},
                            context,
                        )
                    )
                    continue
                if mode == "request":
                    a2a_request_jobs.append(
                        (
                            tc_id,
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
                            tc_id,
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
                        tc_id,
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
    ) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
        """Publish effects and return tool, request, and immediate notify results."""
        tool_ids: dict[str, str] = {}
        request_ids: dict[str, str] = {}
        notify_results: dict[str, Any] = {}
        for tj in split.tool_jobs:
            jid = await self.call(self.bus.tool_job_board.publish, tj)
            tool_ids[tj.tool_call_id] = jid
        if self.bus.a2a_request_job_board is not None:
            for tc_id, request in split.a2a_request_jobs:
                try:
                    jid = await self.call(self.bus.a2a_request_job_board.publish, request)
                    request_ids[tc_id] = jid
                except (LookupError, ValueError) as exc:
                    notify_results[tc_id] = {
                        "success": False,
                        "content": f"A2A request was rejected: {exc}",
                    }
        if self.bus.a2a_notify_job_board is not None:
            for tc_id, notify in split.a2a_notify_jobs:
                try:
                    await self.call(self.bus.a2a_notify_job_board.publish, notify)
                    notify_results[tc_id] = {
                        "success": True,
                        "content": "A2A notification persisted for the target MAGI.",
                    }
                except (LookupError, ValueError) as exc:
                    notify_results[tc_id] = {
                        "success": False,
                        "content": f"A2A notification was rejected: {exc}",
                    }
        return tool_ids, request_ids, notify_results

    # -- gather results + steering -------------------------------------------

    async def _gather_all(
        self,
        ctx: RunContext,
        tool_ids: dict[str, str],  # tool_call_id → job_id
        request_ids: dict[str, str],
        notify_results: dict[str, Any],
    ) -> _GatherResult | None:
        deadline = asyncio.get_running_loop().time() + await self._read_tool_wait()
        tool_timeout: dict[str, str] = dict(tool_ids)  # tc_id → job_id (copy to mutate)
        a2a_timeout: dict[str, str] = dict(request_ids)
        tool_results: dict[str, Any] = {}
        a2a_results: dict[str, Any] = dict(notify_results)
        steering_parts: list[str] = []

        while tool_timeout or a2a_timeout:
            if ctx.cancel_event.is_set():
                break

            # steering
            if ctx.conversation_id and len(steering_parts) < _MAX_STEERING_PARTS:
                steer = await self.call(
                    self.bus.agent_job_board.claim_for_steering,
                    conversation_id=ctx.conversation_id,
                )
                if steer is not None:
                    text = steer.text or ""
                    if text:
                        steering_parts.append(text)
                    from magi.bus.guild.chatNotifyJob import ChatNotifyResult

                    await self.call(
                        self.bus.agent_job_board.submit_result,
                        key=steer.job_id,
                        result=ChatNotifyResult(
                            job_id=steer.job_id,
                            status=JobStatus.COMPLETED,
                        ),
                    )

            # tool results
            for tc_id, job_id in list(tool_timeout.items()):
                r = await self.call(self.bus.tool_job_board.get_result, key=job_id)
                if r is not None:
                    tool_results[tc_id] = r
                    del tool_timeout[tc_id]

            # a2a results
            for tc_id, inv_id in list(a2a_timeout.items()):
                board = self.bus.a2a_request_job_board
                r = await self.call(board.get_result, key=inv_id) if board is not None else None
                if r is not None:
                    a2a_results[tc_id] = r
                    del a2a_timeout[tc_id]

            if not tool_timeout and not a2a_timeout:
                break
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning(
                    "gather timeout, pending_tools=%d pending_a2a=%d",
                    len(tool_timeout),
                    len(a2a_timeout),
                )
                break
            await asyncio.sleep(0.1)

        from magi.bus.guild.runToolJob import RunToolResult, ToolErrorCode

        for tc_id, job_id in tool_timeout.items():
            tool_results[tc_id] = RunToolResult(
                job_id=job_id,
                status=JobStatus.FAILED,
                content="tool execution timed out",
                error_code=ToolErrorCode.FAILED,
                tool_call_id=tc_id,
            )

        from magi.bus.guild.a2aJob import A2AErrorCode, A2ARequestResult

        for tc_id, job_id in a2a_timeout.items():
            a2a_results[tc_id] = A2ARequestResult(
                job_id=job_id,
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
        from magi.bus.guild.runToolJob import ToolErrorCode

        blocks: list[dict] = []
        for tc_id, r in gather.tool_results.items():
            blocks.append(
                {
                    "tool_use_id": tc_id,
                    "type": "tool_result",
                    "content": getattr(r, "content", "") or "",
                    "is_error": getattr(r, "error_code", ToolErrorCode.NONE) != ToolErrorCode.NONE,
                }
            )
        for tc_id, r in gather.a2a_results.items():
            if isinstance(r, dict):
                content = str(r.get("content") or "")
                success = bool(r.get("success", False))
            else:
                content = getattr(r, "content", "") or ""
                success = bool(getattr(r, "success", False))
            blocks.append(
                {
                    "tool_use_id": tc_id,
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
        from magi.bus.guild.deliveryJob import DeliveryJob

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
            self.bus.delivery_job_board.publish,
            DeliveryJob(
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
        from magi.agent.auto_title import request_conversation_title

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

    async def _fallback_reply(self) -> str:
        try:
            replies = await self.call(self.bus.prompt_book.bot_replies)
            return replies.get("agent_no_credentials", "(no credentials)")
        except Exception:
            return "(no credentials)"

    # -- cancel --------------------------------------------------------------

    def _broadcast_cancel(self, conversation_id: str) -> None:
        event = self._in_flight.get(conversation_id)
        if event is not None:
            event.set()

    # -- settings helpers ----------------------------------------------------

    async def _read_max_iterations(self) -> int:
        raw = await self.call(self.bus.settings_book.get, key="agent.max_iterations")
        return _coerce_int(raw, _DEFAULT_MAX_ITERATIONS)

    async def _read_max_tokens(self) -> int:
        raw = await self.call(self.bus.settings_book.get, key="agent.max_tokens")
        return _coerce_int(raw, _DEFAULT_MAX_TOKENS)

    async def _read_tool_wait(self) -> float:
        raw = await self.call(self.bus.settings_book.get, key="agent.tool_wait_seconds")
        return _coerce_float(raw, _DEFAULT_TOOL_WAIT_SECONDS)

    async def _read_llm_timeout(self) -> float:
        raw = await self.call(self.bus.settings_book.get, key="agent.llm_timeout_seconds")
        return _coerce_float(raw, _DEFAULT_LLM_TIMEOUT_SECONDS)


async def submit_agent_message(bus: Bus, message: Any) -> str:
    from magi.bus.guild.chatNotifyJob import ChatNotifyJob

    job = ChatNotifyJob(
        # ``job_id`` is Board-owned; publish() overwrites whatever
        # is here with :meth:`BaseJobBoard.new_job_id`. The cross-
        # message correlation that the previous ``event_id``/
        # ``uuid.uuid4().hex`` fallback was attempting is no longer
        # a concern at this scale.
        conversation_id=getattr(message, "conversation_id", None) or "",
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
