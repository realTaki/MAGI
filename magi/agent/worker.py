"""AgentWorker — bus 上唯一的 agent turn consumer.

设计原则（与 :class:`~magi.tools.worker.ToolsWorker` 、
:class:`~magi.providers.worker.ProvidersWorker` 对齐）：

- **只依赖 bus**。老的 ``magi.bus`` store / facade 一概不碰。
- **构造靠注入**。``AgentWorker(bus: Bus)`` 由 composition root 显式注入。
- **board claim steering**：steering 不通过进程内队列，而是在
  ``_gather_all`` 中主动 ``claim_for_conversation`` 认领同 session 的新
  ChatJob。board 本身是唯一持久化协调点。
- **回复走 delivery_job_board**：``ChatJobResult`` 只承载 success/error_code，
  回复文本统一由 ``_publish_delivery`` 投递。

本步骤已完成 Phase 2 子模块迁移，现已委托调用：
- ``system_prompt.build_system_prompt(bus=...)``
- ``agent_context.build_messages_from_session(bus=...)``
- ``auto_title.request_session_title(bus=...)``
- ``token_usage.record_token_usage(bus=...)``
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from magi.startup.worker import RuntimeWorker

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.callLLMJob import CallLLMResult
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

# A2A feature gate — Phase 2 切真 A2A 后改 False
_A2A_ENABLED = False


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AgentRunFailed(RuntimeError):
    def __init__(self, error_code: str = "agent_run_failed", detail: str = "") -> None:
        self.error_code = error_code
        super().__init__(detail or error_code)


class AgentRunTimedOut(TimeoutError):
    pass


# ---------------------------------------------------------------------------
# RunContext
# ---------------------------------------------------------------------------

@dataclass
class RunContext:
    uid: int | None
    session_id: str | None
    channel: str
    caller_role: str | None
    conversation_id: str
    messages: list[dict] = field(default_factory=list)
    max_iterations: int = _DEFAULT_MAX_ITERATIONS
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    final_reply: str = ""
    final_error: str | None = None
    cancelled: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@dataclass
class _SplitJobs:
    tool_jobs: list = field(default_factory=list)
    a2a_jobs: list = field(default_factory=list)


@dataclass
class _GatherResult:
    tool_results: dict[str, Any] = field(default_factory=dict)
    a2a_results: dict[str, Any] = field(default_factory=dict)
    steering_text: str | None = None


# ---------------------------------------------------------------------------
# AgentWorker
# ---------------------------------------------------------------------------

class AgentWorker(RuntimeWorker):
    """Sequential consumer of one MAGI's ``chat_jobs`` stream."""

    worker_name = "agent"

    def __init__(self, bus: "Bus", *, poll_seconds: float = 0.25) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self.worker_id = f"agent-{uuid.uuid4().hex}"
        self._active_sessions: set[str] = set()
        self._in_flight: dict[str, asyncio.Event] = {}  # conv_id → cancel_event

    # -- main loop -----------------------------------------------------------

    async def _run(self) -> None:
        from magi.bus.guild.chatJob import ChatJobResult

        while not self._stopping:
            job = await self.call(self.bus.agent_job_board.claim)
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            conv_id = getattr(job, "conversation_id", None) or ""

            # cancel
            if getattr(job, "kind", "") == "run.cancel":
                await self.call(self.bus.agent_job_board.submit_result,
                    key=job.event_id,
                    result=ChatJobResult(
                        event_id=job.event_id, success=True, status="completed",
                    ),
                )
                self._broadcast_cancel(conv_id)
                continue

            # steering — release back to board for _process to claim
            if conv_id and conv_id in self._active_sessions:
                await self.call(self.bus.agent_job_board.release, key=job.event_id)
                continue

            # new run
            self._active_sessions.add(conv_id)
            payload = getattr(job, "payload", None) or {}
            run_id = getattr(job, "run_id", "") or ""
            ctx = RunContext(
                uid=payload.get("uid"),
                session_id=payload.get("session_id"),
                channel=payload.get("channel", ""),
                caller_role=payload.get("caller_role"),
                conversation_id=conv_id,
                max_iterations=await self._read_max_iterations(),
            )
            try:
                await self._process(ctx)
            except asyncio.CancelledError:
                ctx.final_error = "magi.run_cancelled"
                raise
            except Exception:
                logger.exception("agent run failed conv=%s", conv_id)
                ctx.final_error = "agent_crashed"
                ctx.final_reply = ctx.final_reply or "抱歉，处理请求时发生了错误。"
                await self._publish_delivery(ctx)
            finally:
                if conv_id:
                    self._active_sessions.discard(conv_id)
                succeeded = ctx.final_error is None
                await self.call(self.bus.agent_job_board.submit_result,
                    key=job.event_id,
                    result=ChatJobResult(
                        event_id=job.event_id,
                        success=succeeded,
                        status="completed" if succeeded else "failed",
                        result={"run_id": run_id} if run_id else None,
                        error_code=ctx.final_error,
                    ),
                )

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
                    ctx.final_error = "llm_timeout"
                    await self._publish_delivery(ctx)
                    return
                if not result.success:
                    ctx.final_reply = "抱歉，回复生成失败，请稍后再试。"
                    ctx.final_error = getattr(result, "error", None) or getattr(result, "error_code", "") or "llm_failed"
                    await self._publish_delivery(ctx)
                    return

                await self._record_token_usage(ctx, result)
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
                tool_ids = await self._publish_effects(split)
                gather = await self._gather_all(ctx, split, tool_ids)
                if gather is None:
                    ctx.final_error = "lease_lost"
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
        if not ctx.session_id or ctx.uid is None:
            return
        from magi.agent.agent_context import build_messages_from_session

        try:
            msgs = await self.call(
                build_messages_from_session,
                uid=ctx.uid, session_id=ctx.session_id,
                new_user_text="", bus=self.bus,
            )
            ctx.messages = list(msgs)  # already list[dict]
        except Exception:
            logger.warning("load_history failed, starting fresh", exc_info=True)

    async def _build_llm_job(self, ctx: RunContext) -> Any:
        """组装完整 LLM 请求。不检查 provider 配置——ProvidersWorker 自己处理。"""
        from magi.bus.guild.callLLMJob import CallLLMJob

        system = await self._system_prompt(ctx)
        messages = [{"role": "system", "content": system}] + list(ctx.messages)
        tools = await self._tool_schemas(ctx.caller_role)

        return CallLLMJob(
            messages=messages, max_tokens=await self._read_max_tokens(),
            tools=tools or None, streaming=False,
            parameters={
                "uid": ctx.uid, "session_id": ctx.session_id,
                "channel": ctx.channel, "caller_role": ctx.caller_role,
            },
        )

    async def _system_prompt(self, ctx: RunContext) -> str:
        from magi.agent.system_prompt import build_system_prompt, read_soul

        try:
            return await self.call(
                lambda: build_system_prompt(
                    uid=ctx.uid or 0, soul=read_soul(bus=self.bus), bus=self.bus,
                )
            )
        except Exception:
            logger.exception("system_prompt build failed; falling back to bare soul")
            return "You are a helpful assistant."

    async def _tool_schemas(self, caller_role: str | None) -> list[dict] | None:
        try:
            defs = await self.call(
                self.bus.tool_definitions_book.list_enabled,
                caller_role=caller_role,
            )
            result = []
            for d in defs or []:
                result.append({
                    "name": getattr(d, "name", ""),
                    "description": getattr(d, "description", ""),
                    "input_schema": getattr(d, "input_schema", {}),
                })
            return result if result else None
        except Exception:
            logger.warning("tool schemas load failed", exc_info=True)
            return None

    # -- LLM wait ------------------------------------------------------------

    async def _wait_for_llm(self, job_id: str) -> "CallLLMResult | None":
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
        tool_call_id: str, tool_name: str, arguments: dict,
        context: dict, catalog_revision: int | None = None,
    ) -> "RunToolJob":
        from magi.bus.guild.runToolJob import RunToolJob
        return RunToolJob(
            tool_call_id=tool_call_id, tool_name=tool_name,
            payload={"arguments": arguments, "context": context},
            catalog_revision=catalog_revision,
        )

    async def _split_tools(self, ctx: RunContext, tool_uses: list[dict]) -> _SplitJobs:
        from magi.bus.guild.sendA2AJob import SendA2AJob

        tool_jobs: list[RunToolJob] = []
        a2a_jobs: list[SendA2AJob] = []
        catalog_state = (
            await self.call(self.bus.tool_catalog_book.get)
            if hasattr(self.bus, "tool_catalog_book") else None
        )
        catalog_revision = catalog_state.revision if catalog_state else 0

        for tu in tool_uses:
            name = tu.get("name", "")
            args = dict(tu.get("input") or {})
            tc_id = str(tu.get("id") or uuid.uuid4().hex)
            context = {
                "workspace": "", "uid": ctx.uid or 0,
                "channel": ctx.channel, "session_id": ctx.session_id or "",
            }
            if name == "message_magi":
                if not _A2A_ENABLED:
                    tool_jobs.append(self._make_tool_job(
                        tc_id, "message_magi",
                        {"_validation_error": "a2a_disabled"},
                        context, catalog_revision,
                    ))
                    continue
                try:
                    target_magic_id = int(args["magic_id"])
                    text = str(args["text"])
                    if target_magic_id <= 0 or not text.strip():
                        raise ValueError("magic_id and text required")
                except (KeyError, TypeError, ValueError) as exc:
                    tool_jobs.append(self._make_tool_job(
                        tc_id, "message_magi",
                        {"_validation_error": str(exc)},
                        context, catalog_revision,
                    ))
                    continue
                a2a_jobs.append(SendA2AJob(
                    tool_call_id=tc_id, target=str(target_magic_id),
                    expect_reply=bool(args.get("expect_reply", False)),
                    request={"text": text, "uid": ctx.uid, "session_id": ctx.session_id},
                ))
            else:
                tool_jobs.append(self._make_tool_job(
                    tc_id, name or "", args, context, catalog_revision,
                ))
        return _SplitJobs(tool_jobs=tool_jobs, a2a_jobs=a2a_jobs)

    # -- publish effects -----------------------------------------------------

    async def _publish_effects(self, split: _SplitJobs) -> dict[str, str]:
        """Publish tool + a2a jobs.  Returns tool_call_id → job_id mapping."""
        tool_ids: dict[str, str] = {}
        for tj in split.tool_jobs:
            jid = await self.call(self.bus.tool_job_board.publish, tj)
            tool_ids[tj.tool_call_id] = jid
        for aj in split.a2a_jobs:
            await self.call(self.bus.a2a_job_board.publish, aj)
        return tool_ids

    # -- gather results + steering -------------------------------------------

    async def _gather_all(
        self,
        ctx: RunContext,
        split: _SplitJobs,
        tool_ids: dict[str, str],  # tool_call_id → job_id
    ) -> _GatherResult | None:
        deadline = asyncio.get_running_loop().time() + await self._read_tool_wait()
        tool_timeout: dict[str, str] = dict(tool_ids)  # tc_id → job_id (copy to mutate)
        a2a_timeout: dict[str, str] = {aj.tool_call_id: aj.invocation_id for aj in split.a2a_jobs}
        tool_results: dict[str, Any] = {}
        a2a_results: dict[str, Any] = {}
        steering_parts: list[str] = []

        while tool_timeout or a2a_timeout:
            if ctx.cancel_event.is_set():
                break

            # steering
            if ctx.conversation_id and len(steering_parts) < _MAX_STEERING_PARTS:
                steer = await self.call(
                    self.bus.agent_job_board.claim_for_conversation,
                    conversation_id=ctx.conversation_id,
                )
                if steer is not None:
                    text = (getattr(steer, "payload", None) or {}).get("text") or ""
                    if text:
                        steering_parts.append(text)
                    from magi.bus.guild.chatJob import ChatJobResult
                    await self.call(self.bus.agent_job_board.submit_result,
                        key=steer.event_id,
                        result=ChatJobResult(
                            event_id=steer.event_id, success=True, status="completed",
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
                r = await self.call(self.bus.a2a_job_board.get_result, key=inv_id)
                if r is not None:
                    a2a_results[tc_id] = r
                    del a2a_timeout[tc_id]

            if not tool_timeout and not a2a_timeout:
                break
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning("gather timeout, pending_tools=%d pending_a2a=%d",
                               len(tool_timeout), len(a2a_timeout))
                break
            await asyncio.sleep(0.1)

        from magi.bus.guild.runToolJob import RunToolResult
        for tc_id, job_id in tool_timeout.items():
            tool_results[tc_id] = RunToolResult(
                job_id=job_id, success=False, content="tool execution timed out",
                is_error=True, tool_call_id=tc_id,
            )

        steering_text = "\n\n".join(steering_parts) if steering_parts else None
        return _GatherResult(
            tool_results=tool_results, a2a_results=a2a_results,
            steering_text=steering_text,
        )

    # -- output --------------------------------------------------------------

    def _append_tool_result_user_message(self, ctx: RunContext, gather: _GatherResult) -> None:
        blocks: list[dict] = []
        for tc_id, r in gather.tool_results.items():
            blocks.append({
                "tool_use_id": tc_id, "type": "tool_result",
                "content": getattr(r, "content", "") or "",
                "is_error": bool(getattr(r, "is_error", False)),
            })
        for tc_id, r in gather.a2a_results.items():
            response = getattr(r, "response", None) or {}
            blocks.append({
                "tool_use_id": tc_id, "type": "tool_result",
                "content": response.get("text", "") if isinstance(response, dict) else "",
                "is_error": not bool(getattr(r, "success", False)),
            })
        if gather.steering_text:
            blocks.append({"type": "text", "text": gather.steering_text})
        ctx.messages.append({
            "role": "user", "content": gather.steering_text or "",
            "content_blocks": blocks,
        })

    async def _publish_delivery(self, ctx: RunContext) -> None:
        from magi.bus.guild.deliveryJob import DeliveryJob
        await self.call(self.bus.delivery_job_board.publish, DeliveryJob(
            channel=ctx.channel,
            payload={
                "text": ctx.final_reply or "处理完毕。",
                "session_id": ctx.session_id, "uid": ctx.uid,
            },
            destination=None,
        ))

    def _maybe_title(self, ctx: RunContext) -> None:
        if not ctx.session_id or ctx.uid is None:
            return
        from magi.agent.auto_title import request_session_title
        self.spawn(
            request_session_title(ctx.uid, ctx.session_id, bus=self.bus),
            name=f"magi-title-{ctx.session_id}",
        )

    # -- helpers -------------------------------------------------------------

    def _build_assistant_message(self, result: "CallLLMResult") -> dict:
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

    async def _record_token_usage(self, ctx: RunContext, result: "CallLLMResult") -> None:
        if not getattr(result, "token_usage", None):
            return
        from magi.agent.token_usage import record_token_usage
        model = getattr(result, "model", "") or ""
        await self.call(
            record_token_usage,
            uid=ctx.uid or 0, channel=ctx.channel,
            provider=model.split(":")[0] if model else "unknown",
            model=model, usage=getattr(result, "token_usage", {}) or {},
            bus=self.bus,
        )

    # -- cancel --------------------------------------------------------------

    def _broadcast_cancel(self, conversation_id: str) -> None:
        for conv_id, event in self._in_flight.items():
            if conv_id == conversation_id:
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


async def submit_agent_message(bus: "Bus", message: Any) -> str:
    from magi.bus.guild.chatJob import ChatJob
    job = ChatJob(
        event_id=getattr(message, "event_id", "") or uuid.uuid4().hex,
        run_id=getattr(message, "target_run_id", None) or f"turn-{uuid.uuid4().hex}",
        conversation_id=getattr(message, "conversation_id", None) or "",
        kind=getattr(message, "kind", "channel.message.received"),
        payload={
            "text": getattr(message, "text", ""),
            "channel": getattr(message, "channel", ""),
            "uid": getattr(message, "uid", None),
            "session_id": getattr(message, "session_id", None),
            "caller_role": getattr(message, "caller_role", None),
        },
    )
    return await asyncio.to_thread(bus.agent_job_board.publish, job)


async def wait_for_agent_run(bus: "Bus", event_id: str, *, timeout_seconds: float = 180.0) -> dict:
    result = await bus.agent_job_board.wait_for_result(key=event_id, timeout=timeout_seconds)
    if result is None:
        raise AgentRunTimedOut(f"agent run {event_id} timed out")
    if not result.success:
        raise AgentRunFailed(error_code=result.error_code or "failed")
    return {"success": True, "error_code": result.error_code, "result": result.result}


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
