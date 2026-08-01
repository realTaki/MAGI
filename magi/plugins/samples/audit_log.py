"""Audit log plugin — writes one structured record per
runtime event to a configurable append-only store.

This is the canonical "show me what the system did"
plugin. It subscribes to the four hooks that constitute
the operator-visible audit trail:

  - ``after_tool_call`` — every tool the LLM called,
    with name, input, result, success/failure.
  - ``after_llm_call`` — every LLM round-trip, with
    provider, model, input/output tokens, error.
  - ``after_channel_send`` — every outbound message
    dispatched through the channel bus, with channel,
    target uid, text, error.
  - ``on_connector_event`` — every external event the
    connector subsystem emitted (forwarded by the
    runtime into the plugin bus).

Other hooks (``before_*``, session hooks) are skipped on
purpose: audit logs are a record of *outcomes*, not of
intent. Logging ``before_tool_call`` would double the
volume and add no information beyond what
``after_tool_call`` already records.

Output sink
-----------

The sink is configurable via ``settings``:

  - ``"path"`` — a file path. Records are appended as
    one JSON object per line (newline-delimited JSON).
    Default: ``<MAGI_DATA_DIR>/audit/audit.log``.

  - ``"store"`` — an in-memory :class:`MemoryAuditStore`
    for tests + the WebUI debug panel. The two sinks are
    not mutually exclusive — both can be set.

  - ``"max_bytes"`` — rotate the file when it exceeds
    this size (default 8 MiB). Rotation keeps the
    existing file as ``<path>.1`` and starts a fresh
    ``<path>``. Best-effort; the operator's logrotate is
    still the source of truth.

  - ``"max_records"`` — cap on the in-memory store
    (default 1000; older records drop FIFO).

Why a sample plugin
-------------------

The audit log is intentionally **in this repo, not
optional**. Operators want to know "what did MAGI do
last Tuesday at 3pm?" out of the box, without installing
a third-party plugin. The plugin is also the reference
implementation every later plugin (cost counter, rate
limiter, webhook fan-out) is expected to follow — same
shape, same subscription pattern, same shutdown contract.
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

from magi.plugins.base import Hook, Plugin, PluginContext
from magi.plugins.bus import HookBus, register_plugin

logger = logging.getLogger("magi.plugins.samples.audit_log")


# -- Record shape ---------------------------------------------------------


@dataclass
class AuditRecord:
    """One row in the audit log.

    JSON-serialisable on purpose — the audit log is meant
    to be readable by anything that can parse JSONL, not
    just Python.
    """

    hook: str
    occurred_at: str
    actor: str
    detail: dict[str, Any]

    def to_json(self) -> str:
        # ``default=str`` keeps odd types (datetime, Path)
        # out of "TypeError: Object of type X is not JSON
        # serializable" territory.
        return json.dumps(
            {
                "hook": self.hook,
                "occurred_at": self.occurred_at,
                "actor": self.actor,
                "detail": self.detail,
            },
            default=str,
            ensure_ascii=False,
        )


# -- Sinks ----------------------------------------------------------------


class MemoryAuditStore:
    """In-memory ring buffer of recent audit records.

    Backs the WebUI's debug panel. Bounded by
    ``max_records``; older records drop FIFO when full.
    Thread/async safe enough for the runtime's "one
    coroutine per handler" model — a single asyncio task
    is the writer; readers can iterate while writers are
    active (deque is documented as thread-safe for
    append/pop, and the audit_log writer only appends).
    """

    def __init__(self, max_records: int = 1000) -> None:
        self._buf: deque[AuditRecord] = deque(maxlen=max_records)

    def append(self, record: AuditRecord) -> None:
        self._buf.append(record)

    def records(self) -> list[AuditRecord]:
        return list(self._buf)


class FileAuditStore:
    """Newline-delimited JSON file with best-effort rotation.

    Synchronous writes; the plugin dispatches via
    ``asyncio.to_thread`` so the runtime hot path stays
    unblocked. Rotation is checked on every write —
    cheap when the file is small.
    """

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
                # Best-effort replace — losing the prior
                # backup is acceptable; losing the active
                # log is not.
                if backup.exists():
                    backup.unlink()
                self._path.rename(backup)
        except OSError as exc:
            logger.warning("audit log rotation failed: %s", exc)


# -- Plugin ---------------------------------------------------------------


def _default_path() -> Path:
    """Resolve the default audit-log path.

    Reads ``MAGI_AUDIT_LOG_PATH`` at call time so an
    operator setting the env var after the plugin module
    is imported still gets the right path.
    """
    return Path(
        os.environ.get("MAGI_AUDIT_LOG_PATH", "/workspace/audit/audit.log"),
    )


class AuditLogPlugin:
    """Audit log plugin.

    Subscribes to :attr:`_HOOKS` and writes one
    :class:`AuditRecord` per emission to each configured
    sink. Synchronous file writes go through
    ``asyncio.to_thread``; the in-memory store appends
    inline.
    """

    name = "audit_log"
    version = "0.1.0"

    _HOOKS = (
        Hook.AFTER_TOOL_CALL,
        Hook.AFTER_LLM_CALL,
        Hook.AFTER_CHANNEL_SEND,
        Hook.ON_CONNECTOR_EVENT,
    )

    def __init__(
        self,
        *,
        file_path: Path | str | None = None,
        max_bytes: int = 8 * 1024 * 1024,
        memory_store: MemoryAuditStore | None = None,
    ) -> None:
        # ``file_path=None`` → use the default path
        # (``MAGI_AUDIT_LOG_PATH`` env var, falling back to
        # ``/workspace/audit/audit.log``). Pass
        # ``file_path=""`` to disable the file sink
        # entirely (used by tests that don't want
        # filesystem writes).
        self._file: FileAuditStore | None = None
        if file_path == "":
            pass  # disabled
        elif file_path is None:
            self._file = FileAuditStore(_default_path(), max_bytes=max_bytes)
        else:
            self._file = FileAuditStore(Path(file_path), max_bytes=max_bytes)
        self._memory = memory_store or MemoryAuditStore()

    def register(self, bus: HookBus) -> None:
        for hook in self._HOOKS:
            bus.subscribe(hook, self._handle)

    def shutdown(self) -> None:
        # No long-lived background tasks. File writes are
        # synchronous per-record; the lock is released as
        # soon as ``append`` returns. Nothing to flush.
        return None

    async def _handle(self, context: PluginContext) -> None:
        record = _build_record(context)
        self._memory.append(record)
        if self._file is not None:
            await self._file.append(record)


# -- Helpers --------------------------------------------------------------


def _build_record(context: PluginContext) -> AuditRecord:
    """Translate a :class:`PluginContext` into an audit row.

    The detail dict is a *whitelist* — we record the
    fields that matter for an audit log and skip the
    noise. The result is JSON-safe.
    """
    hook = context.hook.value
    detail: dict[str, Any] = {}

    if context.hook in (Hook.BEFORE_TOOL_CALL, Hook.AFTER_TOOL_CALL):
        detail = {
            "tool_name": context.tool_name,
            "tool_input": _truncate(context.tool_input),
            "is_error": context.tool_is_error,
            "tool_result": _truncate(context.tool_result),
        }
        actor = "tool"
    elif context.hook in (Hook.BEFORE_LLM_CALL, Hook.AFTER_LLM_CALL):
        detail = {
            "provider": context.llm_provider,
            "model": context.llm_model,
            "input_tokens": context.llm_input_tokens,
            "output_tokens": context.llm_output_tokens,
            "is_error": bool(context.llm_error),
            "error": context.llm_error,
        }
        actor = "llm"
    elif context.hook in (Hook.BEFORE_CHANNEL_SEND, Hook.AFTER_CHANNEL_SEND):
        detail = {
            "channel": context.channel,
            "target_uid": context.channel_target_uid,
            "text": _truncate(context.channel_text),
            "is_error": bool(context.channel_error),
            "error": context.channel_error,
        }
        actor = "channel"
    elif context.hook == Hook.ON_CONNECTOR_EVENT:
        # ``connector_event`` is a ``ConnectorEvent`` — pull
        # the bits that matter without depending on the
        # concrete type.
        ce = context.connector_event
        detail = {
            "connector": getattr(ce, "connector", None),
            "kind": getattr(getattr(ce, "kind", None), "value", None),
            "id": getattr(ce, "id", None),
        }
        actor = "connector"
    else:
        detail = {}
        actor = "runtime"

    return AuditRecord(
        hook=hook,
        occurred_at=context.occurred_at.isoformat()
        if isinstance(context.occurred_at, datetime)
        else str(context.occurred_at),
        actor=actor,
        detail=detail,
    )


def _truncate(value: Any, limit: int = 8192) -> Any:
    """Best-effort truncation of long string fields.

    Audit records should fit comfortably on one line of
    a JSONL log; a multi-MB tool result would make the
    file unsearchable. Truncated values get a sentinel
    suffix so the operator can tell the cut happened.
    """
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "...[truncated]"
    if isinstance(value, dict):
        return {k: _truncate(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v, limit) for v in value]
    return value


# -- Factory registration -------------------------------------------------


def install_audit_log_plugin() -> AuditLogPlugin:
    """Register the audit log plugin into the runtime.

    Idempotent — calling twice replaces the prior
    instance. The runtime calls this from its boot path
    (``magi/__main__.py::run``); tests call it directly
    and keep the returned instance for assertions.
    """
    plugin = AuditLogPlugin()
    register_plugin(plugin)
    return plugin


# Eager-register so ``import magi.plugins.samples.audit_log``
# wires the plugin without an extra boot step. This matches
# the ``install_calendar_connector()`` pattern at the bottom
# of the connectors sample.
install_audit_log_plugin()


__all__ = [
    "AuditLogPlugin",
    "AuditRecord",
    "FileAuditStore",
    "MemoryAuditStore",
    "install_audit_log_plugin",
]