"""Durable tool-effect consumer — bus 上唯一的工具执行点。

孪生结构对齐 :class:`~providers.worker.ProvidersWorker`：

- **只依赖 bus**。老的 bus tool_jobs / tool_catalog 一概不碰。
- **构造靠注入**。Composition root 显式构造并传进来，
  ``concurrency`` 由调用方注入（无环境变量回退）。
- **启动时 publish full tool catalog** — builtin + 所有已注入的外部工具
  (MCP, skills) 写到 ``bus.tool_definitions_book``。
  外部子系统通过 :func:`tools.registry.register_tools` 注入后，
  worker 自动检测并重发布。
- **dumb invoker**。Worker 不区分调用来自 agent turn / 哪个 conversation，
  全走 :class:`RunToolJob` → :class:`RunToolResult`。
- **并发执行**。通过 ``asyncio.Semaphore`` 控制并发槽位，
  默认值 2，通过 ``concurrency`` 构造参数覆盖。

角色菜单过滤在 catalog / agent 侧完成；worker 拿到 job 后按
tool 名查找并执行。Catalog 过期校验（revision / schema_hash）
已移除——schema 不一致时工具执行本身会失败并回传错误。

执行流程
========

::

    start()
      └─ _publish_full_catalog()         # builtin + injected tools → Book
      └─ on_tools_changed(_on_injected_tools_changed)  # 监听注入事件
      └─ spawn _run() task
    _run() loop
      └─ if _catalog_dirty → _publish_full_catalog()  # 工具注入后自动刷新
      └─ bus.tool_job_board.claim(worker_id="worker-1")
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
from typing import TYPE_CHECKING

from old_bus.bases.job import JobStatus
from old_bus.firmwares.books.local import ToolDefinition
from old_bus.firmwares.jobs.runToolJob import RunToolResult, ToolErrorCode
from runtime_worker import RuntimeWorker
from tools.BaseTool import BaseTool, ToolResult
from tools.registry import get_tool

if TYPE_CHECKING:
    from old_bus import Bus
    from old_bus.firmwares.jobs.runToolJob import RunToolJob

logger = logging.getLogger("tools.worker")

#: Stable error codes moved to
#: :class:`~bus.firmwares.jobs.runToolJob.ToolErrorCode` (StrEnum) so the
#: agent layer can treat tool and LLM failures with the same retry
#: logic — see :class:`~bus.firmwares.jobs.callLLMJob.LLMErrorCode` for the
#: mirror on the provider side.

def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _schema_hash(definition: ToolDefinition) -> str:
    """sha256 of canonical JSON over the LLM-visible fields.

    Feeds the per-definition fingerprint used to compute the catalog
    ``snapshot_hash`` (see :meth:`_publish_full_catalog`).
    """
    return hashlib.sha256(
        _canonical_json(
            {
                "name": definition.name,
                "source": definition.source,
                "description": definition.description,
                "input_schema": definition.input_schema,
                "allowed_roles": list(definition.allowed_roles),
                "implementation_version": definition.implementation_version,
            }
        ).encode()
    ).hexdigest()


def _build_definitions_from_tools(
    tools: list[BaseTool],
    source: str,
) -> list[ToolDefinition]:
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
            allowed_roles=list(sorted(tool.ALLOWED_ROLES)),
            enabled=True,
            implementation_version=None,
        )
        # Inline the hash so the worker doesn't have to recompute
        # on every claim.
        d = ToolDefinition(
            name=d.name,
            source=d.source,
            description=d.description,
            input_schema=d.input_schema,
            allowed_roles=d.allowed_roles,
            enabled=d.enabled,
            implementation_version=d.implementation_version,
            schema_hash=_schema_hash(d),
        )
        definitions.append(d)
    return definitions


class ToolsWorker(RuntimeWorker):
    """Consumer that owns every tool execution in a MAGI process.

    Receives a fully-wired :class:`Bus` via constructor injection.
    Publishes the builtin tool catalog at ``start()``, then drains
    :class:`RunToolJob` claims forever.

    ``RuntimeWorker`` owns the common concurrency semaphore.  The claim loop
    reserves capacity, claims one job, then spawns a managed child task; there
    is no fixed worker pool or queue depth limit.
    """

    worker_name = "tools"

    def __init__(
        self,
        bus: Bus,
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds, concurrency=concurrency)
        #: Set by :meth:`_on_injected_tools_changed` when external
        #: subsystems inject tools.  The claim loop checks this
        #: before each claim and republishes the catalog.
        self._catalog_dirty = asyncio.Event()

    async def on_start(self) -> None:
        # Subscribe to runtime tool injection so we can republish
        # the catalog when MCP / skills register their tools.
        from tools.registry import configure, on_tools_changed

        configure(bus=self.bus)
        on_tools_changed(self._on_injected_tools_changed)

        await self._publish_full_catalog()

    async def on_stopped(self) -> None:
        # Background shells are process-local state owned by the
        # bash tools, and this worker is the only thing that
        # outlives an individual tool call — so it's the only place
        # that can tear them down. Without this the subprocesses
        # spawned by ``bash(run_in_background=True)`` survive MAGI's
        # shutdown as orphans. Best-effort: a stuck child must not
        # block the rest of the shutdown chain.
        try:
            from tools.shell._manager import shutdown_background_shells

            await shutdown_background_shells()
        except Exception:
            logger.exception("tools worker: background-shell shutdown failed")

    async def _run(self) -> None:
        while not self._stopping:
            # Republish the catalog if external subsystems
            # injected new tools since the last iteration.
            if self._catalog_dirty.is_set():
                await self._publish_full_catalog()
                self._catalog_dirty.clear()

            await self.reserve_capacity()
            try:
                job = await asyncio.to_thread(
                    self.bus.tool_job_board.claim,
                    worker_id=self.worker_id,
                )
            except Exception:
                self.release_capacity()
                logger.exception("tools worker: claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                self.release_capacity()
                await asyncio.sleep(self.poll_seconds)
                continue

            self.spawn_reserved(self._invoke_safe(job), name=f"tool-job-{job.job_id}")

    async def _invoke_safe(self, job: RunToolJob) -> None:
        """Translate failures into results; RuntimeWorker releases capacity.

        Even if :meth:`_execute` raises an unexpected exception
        (real bug, not a tool-level ``ToolResult(is_error=True)``),
        the shared worker wrapper releases its slot so the loop doesn't
        deadlock.
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
                error_code=ToolErrorCode.CANCELLED,
            )
            raise

    # ----- catalog publish ----------------------------------------------

    async def _publish_full_catalog(self) -> None:
        """Publish builtin + all injected tool definitions to the Book.

        Each source (``"builtin"``, ``"mcp"``, ``"skills"``, …)
        is written in its own ``upsert_many`` call so rows from
        one source never clobber another.  The catalog revision
        is bumped once after all sources are written.
        """
        from tools.registry import _build_tools, list_injected

        # 1. Builtin tools — always present.
        builtin_defs = _build_definitions_from_tools(
            _build_tools(),
            source="builtin",
        )
        await self.call(
            self.bus.tool_definitions_book.upsert_many,
            definitions=builtin_defs,
            source="builtin",
        )

        # 2. Injected tools — one upsert per source.
        total = len(builtin_defs)
        for source, tools in list_injected().items():
            defs = _build_definitions_from_tools(tools, source=source)
            await self.call(
                self.bus.tool_definitions_book.upsert_many,
                definitions=defs,
                source=source,
            )
            total += len(defs)

        # 3. Bump revision + recompute snapshot_hash across ALL
        #    enabled rows (builtin + injected).
        state = await self.call(self.bus.tool_catalog_book.get_current)
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
                r.source,
                r.name,
                hash_by_name.get(r.name, ""),
                int(r.enabled),
                next_revision,
            )
            for r in enabled_rows
        )
        snapshot_hash = hashlib.sha256(_canonical_json(hash_input).encode()).hexdigest()
        await self.call(
            self.bus.tool_catalog_book.replace_snapshot,
            revision=next_revision,
            snapshot_hash=snapshot_hash,
        )
        logger.info(
            "tools worker: published %d tool(s) (catalog revision=%d)",
            total,
            next_revision,
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

    async def _execute(self, job: RunToolJob) -> None:
        tool = get_tool(job.tool_name)
        if tool is None:
            await self._submit_failure(
                job,
                content=f"unknown tool: {job.tool_name!r}",
                error_code=ToolErrorCode.UNKNOWN,
            )
            return

        try:
            result = await tool.run(**_job_arguments(job))
        except Exception as exc:
            logger.exception("tool job %s crashed", job.job_id)
            await self._submit_failure(
                job,
                content=f"tool {job.tool_name!r} crashed: {exc}"[:8000],
                error_code=ToolErrorCode.CRASHED,
            )
            return

        # 3. Submit the result if this worker still owns the lease.  A lease
        #    reclaimed by another worker makes this durable write a no-op.
        await self.call(
            self.bus.tool_job_board.submit_result,
            job_id=job.job_id,
            worker_id=self.worker_id,
            result=_to_result(job, result),
        )

    async def _submit_failure(
        self,
        job: RunToolJob,
        *,
        content: str,
        error_code: ToolErrorCode,
    ) -> None:
        """Submit a failed :class:`RunToolResult`. Swallows submit
        errors so the worker loop never crashes on a transient DB
        blip.  Mirrors
        :meth:`ProvidersWorker._safe_submit_failure`."""
        try:
            await self.call(
                self.bus.tool_job_board.submit_result,
                job_id=job.job_id,
                worker_id=self.worker_id,
                result=RunToolResult(
                    job_id=job.job_id,
                    status=JobStatus.FAILED,
                    content=content[:8000],
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


def _job_arguments(job: RunToolJob) -> dict:
    """Job fields the tool's ``run`` sees: arguments plus per-call identity.

    Bus is constructor-injected. Workspace is ``bus.workspace``.
    """
    arguments = dict(getattr(job, "arguments", None) or {})
    payload = getattr(job, "payload", None)
    if isinstance(payload, dict):
        if not arguments:
            arguments = dict(payload.get("arguments") or {})
        context = dict(payload.get("context") or {})
        for key in ("contact_id", "channel", "conversation_id"):
            if key in context and key not in arguments:
                arguments[key] = context[key]
    conversation_id = getattr(job, "conversation_id", None)
    if conversation_id is not None:
        arguments.setdefault("conversation_id", conversation_id)
    return arguments


def _to_result(job: RunToolJob, result: ToolResult) -> RunToolResult:
    """Map :class:`ToolResult` → :class:`RunToolResult`.

    ``content`` is truncated to 8 KB to fit the column.
    """
    from old_bus.firmwares.jobs.runToolJob import RunToolResult, ToolErrorCode

    return RunToolResult(
        job_id=job.job_id,
        status=JobStatus.COMPLETED if not result.is_error else JobStatus.FAILED,
        content=result.content[:8000],
        error_code=ToolErrorCode.FAILED if result.is_error else ToolErrorCode.NONE,
        tool_call_id=job.tool_call_id,
    )
