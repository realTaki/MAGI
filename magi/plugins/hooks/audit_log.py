"""Audit log hook plugin — replaces the legacy ``magi.plugins.samples.audit_log``.

Subscribes to every OBSERVE HookPoint and writes one JSON-safe
record per envelope to the configured sinks:

  - **File sink**: newline-delimited JSON, rotated at
    ``max_bytes`` (default 8 MiB).
  - **Memory sink**: bounded ring buffer for the WebUI debug
    panel.

The plugin deliberately implements
:class:`magi.plugins.hooks.HookHandlerProtocol` directly so it
serves as the canonical reference for future plugins.

Hook points
-----------

The audit log subscribes to the eight OBSERVE hook points:

  - ``run.transition.committed``
  - ``operation.failed``
  - ``operation.dead_lettered``
  - ``tool.result.received``
  - ``llm.response.received``
  - ``a2a.result.received``
  - ``agent.input.pending``
  - ``delivery.pending``

It does NOT subscribe to ``llm.request.prepared``,
``tool.call.pending``, ``a2a.invocation.pending`` — those are
GATE points, and the audit log is a record of outcomes, not of
intent.

Plugin enablement
-----------------

This plugin is installed by the composition root from the
persistent hook config — it does NOT auto-register at import
time.  Operators toggle it from the WebUI Hooks knowledge page
or ``magi hook enable audit_log``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from magi.bus.hooks.contracts import (
    HookDataScope,
    HookDecision,
    HookEnvelope,
    HookFailureMode,
    HookMode,
    HookPoint,
    HookRegistration,
)
from magi.plugins.hooks.base import HookHandler, hook_handler


logger = logging.getLogger("magi.plugins.hooks.audit_log")


# ───────────────────────────────────────────────────────────────────── #
# Audit record
# ───────────────────────────────────────────────────────────────────── #


@dataclass
class AuditRecord:
    """One row in the audit log.

    JSON-serialisable on purpose — the audit log is meant to be
    readable by anything that can parse JSONL, not just Python.
    """

    hook_point: str
    occurred_at: str
    runtime_id: str
    actor: str
    subject_type: str
    subject_id: str
    decision: str | None
    reason_code: str | None
    risk_score: float | None
    detail: dict[str, Any]
    envelope_metadata: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "hook_point": self.hook_point,
                "occurred_at": self.occurred_at,
                "runtime_id": self.runtime_id,
                "actor": self.actor,
                "subject_type": self.subject_type,
                "subject_id": self.subject_id,
                "decision": self.decision,
                "reason_code": self.reason_code,
                "risk_score": self.risk_score,
                "detail": self.detail,
                "envelope_metadata": self.envelope_metadata,
            },
            default=str,
            ensure_ascii=False,
        )


# ───────────────────────────────────────────────────────────────────── #
# Sinks
# ───────────────────────────────────────────────────────────────────── #


class MemoryAuditStore:
    """Bounded ring buffer backing the WebUI debug panel."""

    def __init__(self, max_records: int = 1000) -> None:
        self._buf: deque[AuditRecord] = deque(maxlen=max_records)

    def append(self, record: AuditRecord) -> None:
        self._buf.append(record)

    def records(self) -> list[AuditRecord]:
        return list(self._buf)


class FileAuditStore:
    """Newline-delimited JSON file with best-effort rotation."""

    def __init__(
        self,
        path: Path,
        max_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def append(self, record: AuditRecord) -> None:
        line = record.to_json() + "\n"
        async with self._lock:
            await asyncio.to_thread(self._write_one, line)

    def _write_one(self, line: str) -> None:
        self._maybe_rotate()
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def _maybe_rotate(self) -> None:
        try:
            if self._path.exists() and self._path.stat().st_size >= self._max_bytes:
                backup = self._path.with_suffix(self._path.suffix + ".1")
                if backup.exists():
                    backup.unlink()
                self._path.rename(backup)
        except OSError as exc:
            logger.warning("audit log rotation failed: %s", exc)


# ───────────────────────────────────────────────────────────────────── #
# Plugin class
# ───────────────────────────────────────────────────────────────────── #


def _default_path() -> Path:
    """Resolve the default audit-log path.

    Reads ``MAGI_AUDIT_LOG_PATH`` at call time so an operator
    setting the env var after the plugin module is imported
    still gets the right path.  When unset, falls back to
    ``<workspace>/logs/audit.log``.
    """
    override = os.environ.get("MAGI_AUDIT_LOG_PATH")
    if override:
        return Path(override)
    from magi.launcher.paths import workspace_dir as _workspace_dir

    return _workspace_dir() / "logs" / "audit.log"


class AuditLogHookHandler:
    """Audit log hook plugin.

    Implements :class:`HookHandlerProtocol` directly so the
    composition root can register it under the BUS hook system
    without any per-plugin code.
    """

    HOOK_POINTS: tuple[HookPoint, ...] = (
        HookPoint.RUN_TRANSITION_COMMITTED,
        HookPoint.OPERATION_FAILED,
        HookPoint.OPERATION_DEAD_LETTERED,
        HookPoint.TOOL_RESULT_RECEIVED,
        HookPoint.LLM_RESPONSE_RECEIVED,
        HookPoint.A2A_RESULT_RECEIVED,
        HookPoint.AGENT_INPUT_PENDING,
        HookPoint.DELIVERY_PENDING,
    )

    def __init__(
        self,
        *,
        file_path: Path | str | None = None,
        max_bytes: int = 8 * 1024 * 1024,
        memory_store: MemoryAuditStore | None = None,
    ) -> None:
        self._file: FileAuditStore | None
        if file_path == "":
            self._file = None
        elif file_path is None:
            self._file = FileAuditStore(_default_path(), max_bytes=max_bytes)
        else:
            self._file = FileAuditStore(Path(file_path), max_bytes=max_bytes)
        self._memory = memory_store or MemoryAuditStore()

    async def handle(self, envelope: HookEnvelope) -> HookDecision | None:
        record = self._build_record(envelope)
        self._memory.append(record)
        if self._file is not None:
            try:
                await self._file.append(record)
            except Exception:
                # Audit must not crash the runtime — log and
                # continue.  The OBSERVE contract is fire-and-
                # forget, so no decision is required.
                logger.exception(
                    "audit log file sink write failed for envelope %s",
                    envelope.hook_event_id,
                )
        return None

    @staticmethod
    def _build_record(envelope: HookEnvelope) -> AuditRecord:
        actor = _actor_for(envelope.hook_point)
        detail = _project_detail(envelope)
        return AuditRecord(
            hook_point=envelope.hook_point.value,
            occurred_at=envelope.occurred_at.isoformat()
            if isinstance(envelope.occurred_at, datetime)
            else str(envelope.occurred_at),
            runtime_id=envelope.runtime.runtime_id,
            actor=actor,
            subject_type=envelope.subject.subject_type,
            subject_id=envelope.subject.subject_id,
            decision=None,
            reason_code=None,
            risk_score=None,
            detail=detail,
            envelope_metadata=dict(envelope.metadata),
        )


def _actor_for(point: HookPoint) -> str:
    return {
        HookPoint.AGENT_INPUT_PENDING: "input",
        HookPoint.LLM_RESPONSE_RECEIVED: "llm",
        HookPoint.TOOL_RESULT_RECEIVED: "tool",
        HookPoint.A2A_RESULT_RECEIVED: "a2a",
        HookPoint.DELIVERY_PENDING: "delivery",
        HookPoint.RUN_TRANSITION_COMMITTED: "run",
        HookPoint.OPERATION_FAILED: "operation",
        HookPoint.OPERATION_DEAD_LETTERED: "operation",
    }.get(point, "runtime")


def _project_detail(envelope: HookEnvelope) -> dict[str, Any]:
    """Project the envelope to a JSON-safe summary."""
    out: dict[str, Any] = {
        "hook_event_id": envelope.hook_event_id,
        "runtime_instance_id": envelope.runtime.runtime_instance_id,
        "environment": envelope.runtime.environment,
        "workspace_id": envelope.runtime.workspace_id,
        "principal_type": envelope.principal.principal_type.value,
        "principal_id": envelope.principal.principal_id,
        "role": envelope.principal.role,
        "correlation_id": envelope.causality.correlation_id,
        "run_id": envelope.causality.run_id,
        "conversation_id": envelope.causality.conversation_id,
        "session_id": envelope.causality.session_id,
    }
    payload = envelope.payload
    if isinstance(payload, dict):
        # Project only the public-safe subset; the materializer
        # already redacted secrets, but we trim further for the
        # audit log so an audit-log reader doesn't accidentally
        # see raw LLM transcript content.
        for key in (
            "tool_name", "tool_source", "tool_call_id",
            "provider", "model", "phase", "status",
            "channel", "destination", "invocation_id", "target",
            "rendered_text", "run_id", "is_error", "attempts",
        ):
            if key in payload:
                value = payload[key]
                if isinstance(value, str) and len(value) > 4096:
                    value = value[:4096] + "...[truncated]"
                out[key] = value
    return out


# ───────────────────────────────────────────────────────────────────── #
# Decorator-built handler — the form the BUS actually registers.
# ───────────────────────────────────────────────────────────────────── #


@hook_handler(
    hook_id="audit_log",
    hook_version="1.0.0",
    hook_points=AuditLogHookHandler.HOOK_POINTS,
    mode=HookMode.OBSERVE,
    priority=1000,  # Low priority — runs after everything else.
    required_scopes=frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.PRINCIPAL_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.RUN_STATE,
    }),
    timeout_ms=500,
    failure_mode=HookFailureMode.FAIL_OPEN,
)
async def audit_log_handler(envelope: HookEnvelope) -> HookDecision | None:
    """Process-one-instance audit log handler.

    The composition root instantiates :class:`AuditLogHookHandler`
    once at boot and passes the resulting instance to the BUS
    via :meth:`bus.hooks.register_handler`.  This module-level
    function is the ``handle`` method bound into the
    :class:`HookHandler` wrapper — see the decorator above.
    """
    # Defer to the class to keep the file/memory sink logic in
    # one place; the decorator wraps the call so the BUS sees
    # a single uniform :class:`HookHandlerProtocol` surface.
    instance = AuditLogHookHandler()
    return await instance.handle(envelope)


__all__ = [
    "AuditLogHookHandler",
    "AuditRecord",
    "FileAuditStore",
    "MemoryAuditStore",
    "audit_log_handler",
]
