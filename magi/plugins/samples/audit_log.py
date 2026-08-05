"""Audit log plugin -- a BUS worker that consumes ``hook_signoffs``.

The OLD design had ``audit_log`` register a synchronous hook
handler that received a :class:`HookEnvelope` for every
observable BUS event.  Under the new tag-based design, the
plugin is a plain BUS worker:

  1. Boot reads the persistent ``hook_plugin_configs`` table to
     discover what hook points it subscribes to.
  2. The worker calls ``bus.store.claim_pending_signoffs(plugin_id)``
     to pull pending signoffs for those hook points.
  3. For each signoff, the worker loads the related subject row
     (LLMAttempt / ToolJob / DeliveryOutbox) and writes one
     JSONL line per event to the audit sink.
  4. The worker calls ``bus.store.ack_signoff(id)`` so the next
     downstream claim can see the subject.

The plugin does not import ``magi.bus.hooks`` -- it talks to
the BUS via the public ``bus.store`` API only.
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

from magi.bus import get_bus
from magi.bus.models.queue import DeliveryOutbox, LLMAttempt, ToolJob
from magi.launcher.hook_config import HookConfigRepository


logger = logging.getLogger("magi.plugins.samples.audit_log")


# Plugin identifier -- must match the ``hook_id`` column in the
# persistent ``hook_plugin_configs`` row.
PLUGIN_ID = "audit_log"


# Default hook points this plugin subscribes to.  Operators may
# override by editing the row's ``hook_points`` JSON column via
# the WebUI; the boot loader re-reads every restart.
DEFAULT_HOOK_POINTS: tuple[str, ...] = (
    "llm.request.prepared",
    "llm.response.received",
    "tool.call.pending",
    "tool.result.received",
    "delivery.pending",
    "delivery.dispatched",
    "run.transition.committed",
    "operation.failed",
    "operation.dead_lettered",
)


# ───────────────────────────────────────────────────────────────────── #
# Audit record
# ───────────────────────────────────────────────────────────────────── #


@dataclass
class AuditRecord:
    """One row in the audit log.

    JSON-serialisable on purpose -- the audit log is meant to be
    readable by anything that can parse JSONL, not just Python.
    """

    timestamp: str
    plugin_id: str
    hook_point: str
    subject_type: str
    subject_id: str
    payload: dict[str, Any]

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "plugin_id": self.plugin_id,
                "hook_point": self.hook_point,
                "subject_type": self.subject_type,
                "subject_id": self.subject_id,
                "payload": self.payload,
            },
            default=str,
            ensure_ascii=False,
        )


# ───────────────────────────────────────────────────────────────────── #
# Plugin worker
# ───────────────────────────────────────────────────────────────────── #


class AuditLogPlugin:
    """Tag-based audit log worker.

    Reads ``hook_signoffs`` rows via
    :meth:`magi.bus.store.BusStore.claim_pending_signoffs` and
    writes one JSONL record per row to the file sink + an
    in-memory ring buffer for the WebUI debug panel.
    """

    def __init__(
        self,
        *,
        plugin_id: str = PLUGIN_ID,
        log_path: Path | None = None,
        ring_size: int = 256,
    ) -> None:
        self.plugin_id = plugin_id
        self.log_path = log_path or Path(
            os.environ.get(
                "MAGI_AUDIT_LOG_PATH",
                "/tmp/magi-audit-log.jsonl",
            )
        )
        self._ring: deque[AuditRecord] = deque(maxlen=ring_size)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._run(), name=f"magi-plugin-{self.plugin_id}"
        )
        logger.info(
            "audit_log plugin started: log_path=%s plugin_id=%s",
            self.log_path, self.plugin_id,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        """Loop: claim pending signoffs, materialise + write, ack."""
        while self._running:
            try:
                rows = await asyncio.to_thread(
                    self._claim_once,
                )
                if not rows:
                    await asyncio.sleep(0.25)
            except Exception:
                logger.exception("audit_log plugin loop error")
                await asyncio.sleep(1.0)

    def _claim_once(self) -> list[AuditRecord]:
        bus = get_bus()
        store = bus.store
        signoffs = store.claim_pending_signoffs(
            self.plugin_id, limit=10,
        )
        records: list[AuditRecord] = []
        for signoff in signoffs:
            record = self._materialise(signoff)
            self._write(record)
            store.ack_signoff(signoff.id)
            records.append(record)
        return records

    def _materialise(self, signoff) -> AuditRecord:
        """Read the related subject row from BUS and build the audit record."""
        bus = get_bus()
        store = bus.store
        state_dir = getattr(store, "_state_dir", None)
        payload: dict[str, Any] = {"hook_point": signoff.hook_point}
        if signoff.subject_type == "llm_attempt":
            request = store.load_llm_job_request(signoff.subject_id) or {}
            payload.update({"request": request})
        elif signoff.subject_type == "tool_job":
            from magi.bus.db.engine import open_session
            with open_session(state_dir) as session:
                row = session.get(ToolJob, signoff.subject_id)
                if row is not None:
                    payload.update({
                        "tool_name": row.tool_name,
                        "arguments": row.payload,
                    })
        elif signoff.subject_type == "delivery_outbox":
            from magi.bus.db.engine import open_session
            with open_session(state_dir) as session:
                row = session.get(DeliveryOutbox, signoff.subject_id)
                if row is not None:
                    payload.update({
                        "channel": row.channel,
                        "destination": row.destination,
                        "status": row.status,
                    })
        return AuditRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            plugin_id=self.plugin_id,
            hook_point=signoff.hook_point,
            subject_type=signoff.subject_type,
            subject_id=signoff.subject_id,
            payload=payload,
        )

    def _write(self, record: AuditRecord) -> None:
        line = record.to_jsonl()
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            logger.exception("audit_log write failed")
        self._ring.append(record)


# ───────────────────────────────────────────────────────────────────── #
# Boot
# ───────────────────────────────────────────────────────────────────── #


async def main() -> None:
    """Boot the audit log plugin worker.

    Reads ``hook_plugin_configs`` to discover what hook points
    to subscribe to.  Without an enabled row the worker stays
    idle (claim_pending_signoffs returns nothing).
    """
    from magi.bus.bootstrap import bootstrap as _bootstrap

    bus = _bootstrap()
    repo = HookConfigRepository(state_dir=str(bus.settings.state_dir))
    config = repo.get_enabled(PLUGIN_ID)
    if config is None:
        logger.info(
            "audit_log plugin: no enabled row in hook_plugin_configs; "
            "skipping boot",
        )
        return

    plugin = AuditLogPlugin(plugin_id=PLUGIN_ID)
    await plugin.start()
    try:
        # Idle until cancelled -- the work happens in the
        # background task created by ``plugin.start``.
        while True:
            await asyncio.sleep(60)
    finally:
        await plugin.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())