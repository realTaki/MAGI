"""Durable tool-effect consumer — new_bus 上唯一的工具执行点。

孪生结构对齐 :class:`~magi.providers.worker.ProvidersWorker`：

- **只依赖 new_bus**。老的 bus tool_jobs / tool_catalog 一概不碰。
- **构造靠注入**。Composition root 显式构造并传进来。
- **启动时 publish builtin tool catalog** 到
  ``bus.tool_definitions_book``（带 schema_hash），写一次就够了，
  代码改动才需要重发。
- **dumb invoker**。Worker 不区分调用来自 agent turn / 哪个 session，
  全走 :class:`RunToolJob` → :class:`RunToolResult`。
- **并发执行**。通过 ``asyncio.Semaphore`` 控制并发槽位，
  ``MAGI_TOOL_CONCURRENCY`` 环境变量覆盖默认值 2。

GATE（enqueue 时由调用方校验角色）已在 publish 之前完成；
worker 拿到 job 时只做 **catalog revision 校验**（防止 agent 拿了
老 schema 后调用），不再重做角色门控（那一步属于 publish 时刻的
权限检查）。

执行流程
========

::

    start()
      └─ _publish_builtin_catalog()     # 一次性把 builtin tools 写进 Book
      └─ spawn _run() task
    _run() loop
      └─ bus.tool_job_board.claim()
      └─ await _slots.acquire()         # 等待并发槽位
      └─ create_task(_invoke_safe(job)) # fire-and-forget
      └─ continue                       # 立即回到循环顶部 claim 下一个

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
import os
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from magi.new_bus.library.local import ToolDefinition
from magi.new_bus.guild.runToolJob import RunToolResult
from magi.tools.base import Tool, ToolContext, ToolResult
from magi.tools.registry import get_tool

if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.runToolJob import RunToolJob

logger = logging.getLogger("magi.tools.worker")

#: Stable short codes for operator-facing error envelopes.
#: Mirrors :mod:`magi.providers.worker` conventions so the agent
#: layer (when it migrates) can treat tool and LLM failures with
#: the same retry logic.
_ERROR_CODES = {
    "catalog_stale": "tool.catalog_stale",
    "unknown": "tool.unknown",
    "crashed": "tool.crashed",
}

#: Default concurrency — how many tool jobs may run simultaneously.
#: Override with ``MAGI_TOOL_CONCURRENCY`` environment variable.
_DEFAULT_CONCURRENCY = 2


def _env_concurrency() -> int:
    """Read ``MAGI_TOOL_CONCURRENCY``, falling back to the default.

    Unset, empty, non-numeric, or < 1 all collapse to
    :data:`_DEFAULT_CONCURRENCY` — a misconfigured env var must not
    take the tool path down.
    """
    raw = os.environ.get("MAGI_TOOL_CONCURRENCY")
    if not raw:
        return _DEFAULT_CONCURRENCY
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "MAGI_TOOL_CONCURRENCY=%r is not an integer; using %d",
            raw, _DEFAULT_CONCURRENCY,
        )
        return _DEFAULT_CONCURRENCY
    if value < 1:
        logger.warning(
            "MAGI_TOOL_CONCURRENCY=%d is below 1; using %d",
            value, _DEFAULT_CONCURRENCY,
        )
        return _DEFAULT_CONCURRENCY
    return value


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


def _build_builtin_definitions() -> list["ToolDefinition"]:
    """One :class:`ToolDefinition` per registered builtin tool.

    Built-in source = ``"builtin"``. MCP tools land in a separate
    Book owned by the MCP worker (not yet built); they're not
    published by this worker.
    """
    # Lazy import: registry defers tool class imports so test
    # isolation works. _build_tools() constructs one of each.
    from magi.tools.registry import _build_tools

    definitions: list[ToolDefinition] = []
    for tool in _build_tools():
        d = ToolDefinition(
            name=tool.name,
            source="builtin",
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


class ToolsWorker:
    """Consumer that owns every tool execution in a MAGI process.

    Receives a fully-wired :class:`NewBus` via constructor injection.
    Publishes the builtin tool catalog at ``start()``, then drains
    :class:`RunToolJob` claims forever.

    Concurrency is controlled by an :class:`asyncio.Semaphore` whose
    size defaults to :data:`_DEFAULT_CONCURRENCY` (2) and can be
    overridden by the ``MAGI_TOOL_CONCURRENCY`` env var.  The claim
    loop is fire-and-forget — it acquires a slot, spawns a child
    :class:`asyncio.Task`, and immediately loops to claim the next
    job.  The semaphore is the throttle; there is no fixed worker
    pool or queue depth limit.
    """

    def __init__(
        self,
        bus: "NewBus",
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        self.bus = bus
        self.poll_seconds = poll_seconds
        # Env is read *here*, not in :func:`start_tool_worker`, so
        # every construction path honours it — a direct
        # ``ToolsWorker(bus)`` (tests, an alternate entry point) got
        # the hard-coded default when the read lived in the factory.
        # Mirrors :class:`~magi.providers.worker.ProvidersWorker`.
        if concurrency is None:
            concurrency = _env_concurrency()
        self.concurrency = max(1, concurrency)
        self._slots = asyncio.Semaphore(self.concurrency)
        self._task: asyncio.Task[None] | None = None
        self._inflight: set[asyncio.Task[None]] = set()
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return

        # 1. Publish the builtin tool catalog. Idempotent — code
        #    changes that add/modify tools are reflected on every
        #    restart. Failure here is logged and swallowed; an
        #    empty catalog just means the agent can't call tools
        #    this session.
        await asyncio.to_thread(self._publish_builtin_catalog)

        self._stopping = False
        self._inflight.clear()
        self._task = asyncio.create_task(self._run(), name="magi-tool-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
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
            task = asyncio.create_task(self._invoke_safe(job))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _invoke_safe(self, job: "RunToolJob") -> None:
        """Wrapper that guarantees ``_slots.release()``.

        Even if :meth:`_execute` raises an unexpected exception
        (real bug, not a tool-level ``ToolResult(is_error=True)``),
        the slot is released so the worker doesn't deadlock.
        Mirrors :meth:`ProvidersWorker._invoke_safe`.
        """
        try:
            await self._execute(job)
        finally:
            self._slots.release()

    # ----- catalog publish ----------------------------------------------

    def _publish_builtin_catalog(self) -> None:
        """Replace the builtin source's snapshot in tool_definitions_book.

        ``source='builtin'`` rows are written atomically (single
        transaction inside :meth:`ToolDefinitionBook.upsert_many`).
        ``source != 'builtin'`` rows (future MCP) are left alone.

        The catalog revision is bumped on every publish so the
        next claim can detect stale-schema calls.
        """
        definitions = _build_builtin_definitions()
        self.bus.tool_definitions_book.upsert_many(
            definitions=definitions, source="builtin",
        )

        # Bump revision + recompute snapshot_hash.
        # snapshot_hash = sha256(canonical_json of (source, name,
        # schema_hash, enabled, revision) for every enabled row).
        state = self.bus.tool_catalog_book.get()
        next_revision = (state.revision + 1) if state else 1
        enabled_rows = self.bus.tool_definitions_book.list_enabled()
        # Re-fetch the schema_hash we computed at definition time —
        # the row only stores spec_json; we keep a parallel dict
        # to recompute. (Hashing is cheap; <1ms for the menu.)
        hash_by_name: dict[str, str] = {d.name: d.schema_hash for d in definitions}
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
        self.bus.tool_catalog_book.replace_snapshot(
            revision=next_revision, snapshot_hash=snapshot_hash,
        )
        logger.info(
            "tools worker: published %d builtin tool(s) "
            "(catalog revision=%d)",
            len(definitions), next_revision,
        )

    # ----- per-job execution --------------------------------------------

    async def _execute(self, job: "RunToolJob") -> None:
        ctx_data = dict(job.payload.get("context") or {})

        # 1. Catalog revision check — did the menu move between
        #    the agent's LLM call and our claim?
        if job.catalog_revision:
            state = self.bus.tool_catalog_book.get()
            current_revision = state.revision if state else 0
            if current_revision > job.catalog_revision:
                self._submit_failure(
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
            definition = self.bus.tool_definitions_book.get_by_name(
                name=job.tool_name,
            )
            if definition is None:
                self._submit_failure(
                    job,
                    content=f"unknown tool: {job.tool_name!r}",
                    error_code=_ERROR_CODES["unknown"],
                )
                return
            current_hash = _schema_hash(definition)
            if current_hash != job.schema_hash:
                self._submit_failure(
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
            self._submit_failure(
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
            uid=int(ctx_data.get("uid") or 0),
            channel=str(ctx_data.get("channel") or ""),
            session_id=str(ctx_data.get("session_id") or ""),
            bus=self.bus,
        )
        denied = tool.gate(ctx)
        if denied:
            self._submit_failure(
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
            self._submit_failure(
                job,
                content=f"tool {job.tool_name!r} crashed: {exc}"[:8000],
                error_code=_ERROR_CODES["crashed"],
            )
            return

        # 5. Submit the result. BaseJobBoard handles attempts ≥
        #    MAX_ATTEMPTS automatically; we don't call retry()
        #    ourselves (unlike the old bus worker).
        self.bus.tool_job_board.submit_result(
            key=job.job_id,
            result=_to_result(job, result),
        )

    def _submit_failure(
        self,
        job: "RunToolJob",
        *,
        content: str,
        error_code: str,
    ) -> None:
        self.bus.tool_job_board.submit_result(
            key=job.job_id,
            result=RunToolResult(
                job_id=job.job_id,
                success=False,
                content=content[:8000],
                is_error=True,
                error=content,
                error_code=error_code,
                run_id=job.run_id,
                tool_call_id=job.tool_call_id,
            ),
        )


# -- module-level singleton (composition root drives the lifecycle) -----

_worker: ToolsWorker | None = None


async def start_tool_worker(
    bus: "NewBus",
    *,
    concurrency: int | None = None,
) -> ToolsWorker:
    """Start the process-local tools worker.

    ``concurrency`` overrides ``MAGI_TOOL_CONCURRENCY``; leave it
    ``None`` to let :class:`ToolsWorker` resolve the env var itself.
    """
    global _worker
    if _worker is None:
        _worker = ToolsWorker(bus=bus, concurrency=concurrency)
        await _worker.start()
    return _worker


async def stop_tool_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


# -- helpers --------------------------------------------------------------


def _to_result(job: "RunToolJob", result: ToolResult) -> "RunToolResult":
    """Map :class:`ToolResult` → :class:`RunToolResult`.

    ``content`` is truncated to 8 KB to fit the column.
    """
    from magi.new_bus.guild.runToolJob import RunToolResult

    return RunToolResult(
        job_id=job.job_id,
        success=not result.is_error,
        content=result.content[:8000],
        is_error=result.is_error,
        run_id=job.run_id,
        tool_call_id=job.tool_call_id,
    )
