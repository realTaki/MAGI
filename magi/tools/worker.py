"""Durable tool-effect consumer owned by :mod:`magi.tools`.

The worker never imports channel implementations or the agent loop.  It
claims a persisted job, performs the external/local effect outside a database
transaction, then emits a durable ``tool.result`` inbox event for the agent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from pathlib import Path

from magi.bus import ToolClaim, ToolContext, ToolDefinition, get_bus
from magi.bus.hooks.contracts import HookAction
from magi.tools.registry import get_tool

logger = logging.getLogger("magi.tools.worker")


def _seed_tools() -> None:
    """Publish the executable registry as an atomic BUS catalog snapshot.

    Loads both built-in tools and MCP-discovered tools, then publishes
    them into the durable Tool Catalog.  The Agent reads from that catalog
    and never knows whether a schema came from built-in code or MCP.
    """
    try:
        from magi.tools.registry import bootstrap_mcp_tools, get_tools_grouped

        bus = get_bus()
        # Ensure MCP tools are loaded before we snapshot the grouped list.
        bootstrap_mcp_tools()
        builtin, mcp = get_tools_grouped()
        for source, tools in (("builtin", builtin), ("mcp", mcp)):
            snapshot = bus.tool_catalog.get_snapshot()
            published = bus.tool_catalog.replace_snapshot(
                source=source,
                expected_previous_revision=snapshot.revision,
                definitions=[
                    ToolDefinition(
                        name=tool.name,
                        source=source,
                        description=tool.description,
                        input_schema=dict(tool.input_schema),
                        allowed_roles=tuple(sorted(tool.ALLOWED_ROLES)),
                        implementation_version=None,
                    )
                    for tool in tools
                ],
            )
            logger.info("tool catalog published source=%s revision=%d tools=%d", source, published.revision, len(tools))
    except Exception:
        logger.exception("tool catalog publish failed — agent may see stale schemas")


class ToolWorker:
    """Single durable tool consumer for one MAGI process."""

    def __init__(self, *, poll_seconds: float = 0.25) -> None:
        self.bus = get_bus()
        self.worker_id = f"tools-{uuid.uuid4().hex}"
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            # Publish the durable catalog before the poll loop starts. The
            # replacement is idempotent — code
            # changes that add/modify tools are reflected on restart.
            _seed_tools()
            self._task = asyncio.create_task(self._run(), name="magi-tool-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            claim = self.bus.tool_jobs.claim_next(self.worker_id)
            if claim is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._execute(claim)

    async def _execute(self, claim: ToolClaim) -> None:
        context_data = dict(claim.payload.get("context") or {})
        if claim.schema_hash:
            definition = self.bus.tool_catalog.get_definition(claim.tool_name, source=claim.source)
            if (
                definition is None
                or not definition.enabled
                or definition.schema_hash != claim.schema_hash
                or definition.revision < (claim.catalog_revision or 0)
            ):
                self.bus.tool_jobs.complete(
                    claim,
                    content=f"tool {claim.tool_name!r} is no longer available for this catalog snapshot",
                    is_error=True,
                )
                self.bus.tool_jobs.retry(claim.job_id)
                return
        tool = get_tool(claim.tool_name, caller_role=context_data.get("caller_role"))
        if tool is None:
            self.bus.tool_jobs.complete(
                claim, content=f"unknown or unauthorized tool: {claim.tool_name!r}", is_error=True
            )
            self.bus.tool_jobs.retry(claim.job_id)
            return

        # GATE — call ``bus.hooks.evaluate(TOOL_CALL_PENDING)``
        # before invoking ``tool.run``.  A DENY blocks the call
        # without ever reaching the executor; the worker writes a
        # synthetic failed result so the actor loop can resume.
        decision = await self._evaluate_tool_call_gate(claim, context_data)
        if decision is HookAction.DENY:
            self.bus.tool_jobs.complete(
                claim,
                content=f"tool {claim.tool_name!r} denied by hook policy",
                is_error=True,
            )
            return  # No retry — a DENY is final.
        try:
            result = await tool.run(
                ToolContext(
                    workspace=str(context_data.get("workspace") or ""),
                    uid=int(context_data.get("uid") or 0),
                    channel=str(context_data.get("channel") or ""),
                    session_id=str(context_data.get("session_id") or ""),
                ),
                **dict(claim.payload.get("arguments") or {}),
            )
            content = result.content[:8000]
            is_error = result.is_error
        except Exception as exc:  # tools report errors back to the actor
            logger.exception("tool job %s failed", claim.job_id)
            content = f"tool {claim.tool_name!r} crashed: {exc}"[:8000]
            is_error = True
        self.bus.tool_jobs.complete(claim, content=content, is_error=is_error)
        if is_error:
            self.bus.tool_jobs.retry(claim.job_id)
        # OBSERVE — the tool result is durable by the time we get
        # here; we emit ``TOOL_RESULT_RECEIVED`` for the audit log
        # (and any future OBSERVE handlers) without blocking.
        try:
            from magi.bus.hooks.contracts import (
                EvaluationRequest,
                HookDataClassification,
                HookPoint,
                PrincipalHookContext,
                PrincipalType,
                RuntimeHookContext,
                SecurityHookContext,
            )

            now = _now_naive()
            await self.bus.hooks.publish_observation(
                EvaluationRequest(
                    hook_point=HookPoint.TOOL_RESULT_RECEIVED,
                    subject_type="tool_job",
                    subject_id=claim.job_id,
                    requested_by=self.worker_id,
                    runtime=RuntimeHookContext(
                        magi_id=None, magis_id=None,
                        runtime_id="tool-worker",
                        runtime_instance_id=self.worker_id,
                        environment="runtime",
                        workspace_id="default",
                    ),
                    principal=PrincipalHookContext(
                        principal_type=PrincipalType.SYSTEM,
                        principal_id=self.worker_id,
                        role=context_data.get("caller_role"),
                        permissions=(),
                        membership_id=None,
                        source_type="tool",
                        source_id=claim.tool_name,
                    ),
                    security=SecurityHookContext(
                        attempt=claim.attempts, deadline=None,
                        created_at=now, available_at=now,
                        policy_labels=(), security_labels=(),
                        data_classification=HookDataClassification.INTERNAL,
                    ),
                    metadata={
                        "tool_name": claim.tool_name,
                        "is_error": is_error,
                        "content_size": len(content or ""),
                    },
                )
            )
        except Exception:
            # Observation must never block tool completion.
            logger.exception("tool_result_received observation failed")

    async def _evaluate_tool_call_gate(
        self, claim: ToolClaim, context_data: dict,
    ) -> "HookAction":
        """Run the ``TOOL_CALL_PENDING`` GATE handlers."""
        try:
            from magi.bus.hooks.contracts import (
                EvaluationRequest,
                HookAction,
                HookDataClassification,
                HookPoint,
                PrincipalHookContext,
                PrincipalType,
                RuntimeHookContext,
                SecurityHookContext,
            )

            now = _now_naive()
            result = await self.bus.hooks.evaluate(
                EvaluationRequest(
                    hook_point=HookPoint.TOOL_CALL_PENDING,
                    subject_type="tool_job",
                    subject_id=claim.job_id,
                    requested_by=self.worker_id,
                    runtime=RuntimeHookContext(
                        magi_id=None, magis_id=None,
                        runtime_id="tool-worker",
                        runtime_instance_id=self.worker_id,
                        environment="runtime",
                        workspace_id="default",
                    ),
                    principal=PrincipalHookContext(
                        principal_type=PrincipalType.SYSTEM,
                        principal_id=self.worker_id,
                        role=context_data.get("caller_role"),
                        permissions=(),
                        membership_id=None,
                        source_type="tool",
                        source_id=claim.tool_name,
                    ),
                    security=SecurityHookContext(
                        attempt=claim.attempts, deadline=None,
                        created_at=now, available_at=now,
                        policy_labels=(), security_labels=(),
                        data_classification=HookDataClassification.INTERNAL,
                    ),
                    metadata={"tool_name": claim.tool_name},
                )
            )
            return result.decision
        except Exception:
            logger.exception(
                "TOOL_CALL_PENDING evaluation failed for %s; fail-open",
                claim.job_id,
            )
            return HookAction.ALLOW


_worker: ToolWorker | None = None


def _now_naive():
    """Return the current UTC time as a naive datetime.

    Inlined so the worker module doesn't import from
    ``magi.bus.db.base`` (architecture test forbids it).
    """
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


async def start_tool_worker() -> ToolWorker:
    global _worker
    if _worker is None:
        _worker = ToolWorker()
        await _worker.start()
    return _worker


async def stop_tool_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None
