"""Durable tool-effect consumer — bus 上唯一的工具执行点。

孪生结构对齐 :class:`~magi.providers.worker.ProvidersWorker`：

- **只依赖 bus**。老的 bus tool_jobs / tool_catalog 一概不碰。
- **构造靠注入**。Composition root 显式构造并传进来，
  ``concurrency`` 由调用方注入（无环境变量回退）。
- **启动时 publish full tool catalog** — builtin + 所有已注入的外部工具
  (MCP, skills) 写到 ``bus.tool_definitions_book``。
  外部子系统通过 :func:`magi.tools.registry.register_tools` 注入后，
  worker 自动检测并重发布。
- **dumb invoker**。Worker 不区分调用来自 agent turn / 哪个 session，
  全走 :class:`RunToolJob` → :class:`RunToolResult`。
- **并发执行**。通过 ``asyncio.Semaphore`` 控制并发槽位，
  默认值 2，通过 ``concurrency`` 构造参数覆盖。

GATE（enqueue 时由调用方校验角色）已在 publish 之前完成；
worker 拿到 job 时只做 **catalog revision 校验**（防止 agent 拿了
老 schema 后调用），不再重做角色门控（那一步属于 publish 时刻的
权限检查）。

执行流程
========

::

    start()
      └─ _publish_full_catalog()         # builtin + injected tools → Book
      └─ on_tools_changed(_on_injected_tools_changed)  # 监听注入事件
      └─ spawn _run() task
    _run() loop
      └─ if _catalog_dirty → _publish_full_catalog()  # 工具注入后自动刷新
      └─ bus.tool_job_board.claim()
      └─ await _slots.acquire()
      └─ create_task(_invoke_safe(job))  # fire-and-forget
      └─ continue

入队 helper
===========

调用方直接 ``bus.tool_job_board.publish(RunToolJob(...))``。本模块
不提供 helper —— 与 providers 模式一致。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from magi.bus.library.local import ToolDefinition
from magi.bus.guild.runToolJob import RunToolResult
from magi.tools.base import Tool, ToolContext, ToolResult
from magi.tools.registry import get_tool
from magi.runtime_worker import RuntimeWorker

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.runToolJob import RunToolJob

logger = logging.getLogger("magi.tools.worker")

#: Stable short codes for operator-facing error envelopes.
#: Mirrors :mod:`magi.providers.worker` conventions so the agent
#: layer (when it migrates) can treat tool and LLM failures with
#: the same retry logic.
_ERROR_CODES = {
    "catalog_stale": "tool.catalog_stale",
    "unknown": "tool.unknown",
    "crashed": "tool.crashed",
    "cancelled": "tool.cancelled",
}

#: Default concurrency — how many tool jobs may run simultaneously.
#: Override by passing ``concurrency`` to the constructor.
_DEFAULT_CONCURRENCY = 2


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _schema_hash(definition: "ToolDefinition") -> str:
    """sha256 of canonical JSON over the LLM-visible fields.

    The hash is what the worker compares against the
    ``schema_hash`` on a claimed :class:`RunToolJob`; a mismatch
    means the agent's menu was stale when it enqueued the call.
    """
    return hashlib.sha256(
        _canonical_json({
            "name": definition.name,
            "source": definition.source,
            "description": definition.description,
            "input_schema": definition.input_schema,
            "allowed_roles": list(definition.allowed_roles),
            "implementation_version": definition.implementation_version,
        }).encode()
    ).hexdigest()


def _build_definitions_from_tools(
    tools: list["Tool"],
    source: str,
) -> list["ToolDefinition"]:
    """Build :class:`ToolDefinition` rows from concrete tool instances.

    Used by :meth:`ToolsWorker._publish_full_catalog` for both
    builtin and injected sources.
    """
    definitions: list[ToolDefinition] = []
    for tool in tools:
        d = ToolDefinition(
            name=tool.name,
            source=source,
            description=tool.description,
            input_schema=dict(tool.input_schema),
            allowed_roles=tuple(sorted(tool.ALLOWED_ROLES)),
            enabled=True,
            implementation_version=None,
        )
        # Inline the hash so the worker doesn't have to recompute
        # on every claim.
        d = ToolDefinition(
            name=d.name, source=d.source, description=d.description,
            input_schema=d.input_schema, allowed_roles=d.allowed_roles,
            enabled=d.enabled, implementation_version=d.implementation_version,
            schema_hash=_schema_hash(d),
        )
        definitions.append(d)
    return definitions


class ToolsWorker(RuntimeWorker):
    """Consumer that owns every tool execution in a MAGI process.

    Receives a fully-wired :class:`Bus` via constructor injection.
    Publishes the builtin tool catalog at ``start()``, then drains
    :class:`RunToolJob` claims forever.

    Concurrency is controlled by an :class:`asyncio.Semaphore` whose
    size defaults to :data:`_DEFAULT_CONCURRENCY` (2) and can be
    overridden via the ``concurrency`` constructor parameter. The
    claim loop is fire-and-forget — it acquires a slot, spawns a
    child :class:`asyncio.Task`, and immediately loops to claim the
    next job.  The semaphore is the throttle; there is no fixed
    worker pool or queue depth limit.
    """

    worker_name = "tools"

    def __init__(
        self,
        bus: "Bus",
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        # Concurrency is constructor-injected only — no env var
        # fallback. The startup module reads any env-configured
        # override and passes it in. Mirrors
        # :class:`~magi.providers.worker.ProvidersWorker`.
        self.concurrency = max(1, concurrency or _DEFAULT_CONCURRENCY)
        self._slots = asyncio.Semaphore(self.concurrency)
        self._inflight: set[asyncio.Task[None]] = set()
        #: Set by :meth:`_on_injected_tools_changed` when external
        #: subsystems inject tools.  The claim loop checks this
        #: before each claim and republishes the catalog.
        self._catalog_dirty = asyncio.Event()

    async def on_start(self) -> None:
        # Subscribe to runtime tool injection so we can republish
        # the catalog when MCP / skills register their tools.
        from magi.tools.registry import on_tools_changed

        on_tools_changed(self._on_injected_tools_changed)

        # 1. Publish the full tool catalog (builtin + any
        #    already-injected tools). Called synchronously
        #    (mirrors ProvidersWorker._publish_provider_options).
        await self._publish_full_catalog()

        self._inflight.clear()

    async def on_stopped(self) -> None:
        # Wait for any still-running child tasks before the event
        # loop tears down.  Semaphore prevents new tasks from
        # spawning after _run() exits; inflight drains what's
        # already in flight.
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
            self._inflight.clear()

        # Background shells are process-local state owned by the
        # bash tools, and this worker is the only thing that
        # outlives an individual tool call — so it's the only place
        # that can tear them down. Without this the subprocesses
        # spawned by ``bash(run_in_background=True)`` survive MAGI's
        # shutdown as orphans. Best-effort: a stuck child must not
        # block the rest of the shutdown chain.
        try:
            from magi.tools.shell._manager import shutdown_background_shells

            await shutdown_background_shells()
        except Exception:
            logger.exception(
                "tools worker: background-shell shutdown failed"
            )

    async def _run(self) -> None:
        while not self._stopping:
            # Republish the catalog if external subsystems
            # injected new tools since the last iteration.
            if self._catalog_dirty.is_set():
                await self._publish_full_catalog()
                self._catalog_dirty.clear()

            try:
                job = await asyncio.to_thread(
                    self.bus.tool_job_board.claim,
                )
            except Exception:
                logger.exception("tools worker: claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            # Acquire a concurrency slot before spawning.  The
            # loop blocks here when all slots are busy — no
            # further claims happen until a slot frees up.
            await self._slots.acquire()
            task = self.spawn(self._invoke_safe(job), name=f"tool-job-{job.job_id}")
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _invoke_safe(self, job: "RunToolJob") -> None:
        """Wrapper that guarantees ``_slots.release()``.

        Even if :meth:`_execute` raises an unexpected exception
        (real bug, not a tool-level ``ToolResult(is_error=True)``),
        the slot is released so the worker doesn't deadlock.
        On cancellation, a failure result is submitted before
        re-raising so the caller doesn't wait forever.
        Mirrors :meth:`ProvidersWorker._invoke_safe`.
        """
        try:
            await self._execute(job)
        except asyncio.CancelledError:
            await self._submit_failure(
                job,
                content="tools worker cancelled",
                error_code=_ERROR_CODES["cancelled"],
            )
            raise
        finally:
            self._slots.release()

    # ----- catalog publish ----------------------------------------------

    async def _publish_full_catalog(self) -> None:
        """Publish builtin + all injected tool definitions to the Book.

        Each source (``"builtin"``, ``"mcp"``, ``"skills"``, …)
        is written in its own ``upsert_many`` call so rows from
        one source never clobber another.  The catalog revision
        is bumped once after all sources are written.
        """
        from magi.tools.registry import _build_tools, list_injected

        # 1. Builtin tools — always present.
        builtin_defs = _build_definitions_from_tools(
            _build_tools(), source="builtin",
        )
        await self.call(self.bus.tool_definitions_book.upsert_many,
            definitions=builtin_defs, source="builtin",
        )

        # 2. Injected tools — one upsert per source.
        total = len(builtin_defs)
        for source, tools in list_injected().items():
            defs = _build_definitions_from_tools(tools, source=source)
            await self.call(self.bus.tool_definitions_book.upsert_many,
                definitions=defs, source=source,
            )
            total += len(defs)

        # 3. Bump revision + recompute snapshot_hash across ALL
        #    enabled rows (builtin + injected).
        state = await self.call(self.bus.tool_catalog_book.get)
        next_revision = (state.revision + 1) if state else 1
        enabled_rows = await self.call(self.bus.tool_definitions_book.list_enabled)
        # Build a hash map from the definitions we just computed.
        hash_by_name: dict[str, str] = {}
        for d in builtin_defs:
            hash_by_name[d.name] = d.schema_hash
        for source, tools in list_injected().items():
            for d in _build_definitions_from_tools(tools, source=source):
                hash_by_name[d.name] = d.schema_hash
        hash_input = sorted(
            (
                r.source, r.name, hash_by_name.get(r.name, ""),
                int(r.enabled), next_revision,
            )
            for r in enabled_rows
        )
        snapshot_hash = hashlib.sha256(
            _canonical_json(hash_input).encode()
        ).hexdigest()
        await self.call(self.bus.tool_catalog_book.replace_snapshot,
            revision=next_revision, snapshot_hash=snapshot_hash,
        )
        logger.info(
            "tools worker: published %d tool(s) (catalog revision=%d)",
            total, next_revision,
        )

    def _on_injected_tools_changed(self) -> None:
        """Registry listener — fires when an external subsystem
        calls :func:`register_tools`.

        Thread-safe: :class:`asyncio.Event` is safe to
        :meth:`~asyncio.Event.set` from any thread.  The claim
        loop picks this up on its next iteration.
        """
        self._catalog_dirty.set()

    async def refresh_catalog(self) -> None:
        """Force immediate republish of the full tool catalog.

        External callers use this after injecting tools when
        they need the new definitions visible before the claim
        loop's next natural iteration (e.g. in tests).
        """
        await self._publish_full_catalog()

    # ----- per-job execution --------------------------------------------

    async def _execute(self, job: "RunToolJob") -> None:
        ctx_data = dict(job.payload.get("context") or {})

        # 1. Catalog revision check — did the menu move between
        #    the agent's LLM call and our claim?
        if job.catalog_revision:
            state = await self.call(self.bus.tool_catalog_book.get)
            current_revision = state.revision if state else 0
            if current_revision > job.catalog_revision:
                await self._submit_failure(
                    job,
                    content=(
                        f"tool {job.tool_name!r}: catalog moved "
                        f"forward (claimed at r{job.catalog_revision}, "
                        f"current r{current_revision})"
                    ),
                    error_code=_ERROR_CODES["catalog_stale"],
                )
                return

        # 2. schema_hash check — did this specific tool's schema
        #    change between enqueue and claim?
        #
        #    The Book hands back a ``ToolDefinition`` with the same
        #    semantic fields the publish path hashed, so we re-run
        #    :func:`_schema_hash` on it directly. ``schema_hash`` is
        #    not a stored column — recomputing is the contract.
        if job.schema_hash:
            definition = await self.call(self.bus.tool_definitions_book.get_by_name,
                name=job.tool_name,
            )
            if definition is None:
                await self._submit_failure(
                    job,
                    content=f"unknown tool: {job.tool_name!r}",
                    error_code=_ERROR_CODES["unknown"],
                )
                return
            current_hash = _schema_hash(definition)
            if current_hash != job.schema_hash:
                await self._submit_failure(
                    job,
                    content=(
                        f"tool {job.tool_name!r}: schema changed since "
                        f"agent enqueued (claimed hash {job.schema_hash[:8]}, "
                        f"current {current_hash[:8]})"
                    ),
                    error_code=_ERROR_CODES["catalog_stale"],
                )
                return

        # 3. Look up the tool by name. Role gating happens in
        #    ``tool.gate(ctx)`` below — registry dispatch is
        #    no longer role-aware (the menu filter lives on
        #    the agent side via the catalog, not here).
        tool = get_tool(job.tool_name)
        if tool is None:
            await self._submit_failure(
                job,
                content=f"unknown tool: {job.tool_name!r}",
                error_code=_ERROR_CODES["unknown"],
            )
            return

        # 4. Build execution context and run the runtime gate.
        #    ``Tool.gate`` re-resolves the caller's role from
        #    ``ctx.bus.contacts_book`` on every call, so we
        #    don't carry a stale role on the context.
        ctx = ToolContext(
            workspace=str(ctx_data.get("workspace") or ""),
            contact_id=int(ctx_data.get("contact_id") or 0),
            channel=str(ctx_data.get("channel") or ""),
            conversation_id=str(ctx_data.get("conversation_id") or ""),
            bus=self.bus,
        )
        denied = tool.gate(ctx)
        if denied:
            await self._submit_failure(
                job,
                content=denied,
                error_code="tool.unauthorized",
            )
            return

        # 5. Execute. The worker MUST NOT raise to surface
        #    "expected failure" — Tool.run() returns ToolResult
        #    with is_error=True in that case. Real bugs raise;
        #    we catch and translate.
        try:
            result = await tool.run(
                ctx,
                **dict(job.payload.get("arguments") or {}),
            )
        except Exception as exc:
            logger.exception("tool job %s crashed", job.job_id)
            await self._submit_failure(
                job,
                content=f"tool {job.tool_name!r} crashed: {exc}"[:8000],
                error_code=_ERROR_CODES["crashed"],
            )
            return

        # 5. Submit the result. BaseJobBoard handles attempts ≥
        #    MAX_ATTEMPTS automatically; we don't call retry()
        #    ourselves.
        await self.call(self.bus.tool_job_board.submit_result,
            key=job.job_id,
            result=_to_result(job, result),
        )

    async def _submit_failure(
        self,
        job: "RunToolJob",
        *,
        content: str,
        error_code: str,
    ) -> None:
        """Submit a failed :class:`RunToolResult`. Swallows submit
        errors so the worker loop never crashes on a transient DB
        blip.  Mirrors
        :meth:`ProvidersWorker._safe_submit_failure`."""
        try:
            await self.call(self.bus.tool_job_board.submit_result,
                key=job.job_id,
                result=RunToolResult(
                    job_id=job.job_id,
                    success=False,
                    content=content[:8000],
                    is_error=True,
                    error=content,
                    error_code=error_code,
                    tool_call_id=job.tool_call_id,
                ),
            )
        except Exception:
            logger.exception(
                "tools worker: failed to submit failure for %s",
                job.job_id,
            )


# -- helpers --------------------------------------------------------------


def _to_result(job: "RunToolJob", result: ToolResult) -> "RunToolResult":
    """Map :class:`ToolResult` → :class:`RunToolResult`.

    ``content`` is truncated to 8 KB to fit the column.
    """
    from magi.bus.guild.runToolJob import RunToolResult

    return RunToolResult(
        job_id=job.job_id,
        success=not result.is_error,
        content=result.content[:8000],
        is_error=result.is_error,
        tool_call_id=job.tool_call_id,
    )
