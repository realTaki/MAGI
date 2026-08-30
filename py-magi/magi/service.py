"""One MAGI: BUS, workers, and an ASP client onto webapp/asp."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any

from bus import (
    BaseWorker,
    Bus,
    ChatNotify,
    CreateConversationJob,
    DeliveryNotify,
    DeliveryNotifyResult,
    JobStatus,
)
from magi.asp import AspClient
from magi.constant import WORKERS, workspace_path

logger = logging.getLogger("magi")

_RESULT_TIMEOUT = 5.0


class Magi:
    """Own one BUS, its workers, and one ASP connection."""

    def __init__(
        self,
        handle: str,
        base: str,
        token: str,
        *,
        worker_types: Sequence[type[BaseWorker]] = WORKERS,
    ) -> None:
        self.handle = handle
        self.workspace = workspace_path(handle)
        self.bus = Bus(self.workspace)
        self.asp = AspClient(handle=handle, base=base, token=token)
        self._worker_types = tuple(worker_types)
        self._workers: dict[str, BaseWorker] = {}
        self._closed = False
        self._conversations: dict[str, int] = {}
        self._sessions: dict[int, str] = {}

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def run(self) -> bool:
        """Attach every configured worker to this MAGI's shared BUS."""
        if self._closed:
            raise ValueError("Magi is closed")
        if self._workers:
            raise ValueError("already running")

        prepared: list[tuple[str, BaseWorker]] = []
        for worker_type in self._worker_types:
            worker_id = worker_type.worker_name
            if not worker_id:
                raise ValueError(f"{worker_type.__qualname__} needs worker_name")
            prepared.append((worker_id, worker_type()))
        if not prepared:
            raise ValueError("no workers")
        if len({worker_id for worker_id, _ in prepared}) != len(prepared):
            raise ValueError("duplicate worker_id")

        attached: dict[str, BaseWorker] = {}
        for worker_id, worker in prepared:
            if not worker.attach(self.bus):
                worker.detach()
                self._detach_workers(attached)
                return False
            attached[worker_id] = worker
        self._workers = attached
        return True

    def serve(self) -> None:
        """Attach workers, then stay on ASP /connect until interrupted."""
        if not self.run():
            self.close()
            raise RuntimeError("MAGI could not attach its configured workers")
        try:
            asyncio.run(self._serve_asp())
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def shutdown(self) -> None:
        """Detach workers while retaining the BUS for controlled reuse."""
        self._detach_workers(self._workers)
        self._workers = {}

    def close(self) -> None:
        if self._closed:
            return
        self.shutdown()
        self.bus.close()
        self._closed = True

    def __enter__(self) -> Magi:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    async def _serve_asp(self) -> None:
        ready = asyncio.Event()
        await asyncio.gather(
            self.asp.listen(self._on_event, ready=ready),
            self._pump_delivery(ready),
        )

    async def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        session_id = event.get("session_id")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if not isinstance(session_id, str):
            return
        try:
            if kind == "session.invited" and payload.get("invitee") == self.handle:
                await self.asp.join(session_id)
                initial = payload.get("initial_message")
                if isinstance(initial, dict):
                    await asyncio.to_thread(self._ingest, session_id, initial)
            elif kind == "session.message" and payload.get("sender") != self.handle:
                await asyncio.to_thread(self._ingest, session_id, payload)
        except Exception:
            logger.exception("ASP event %s on %s failed", kind, session_id)

    def _ingest(self, session_id: str, payload: dict[str, Any]) -> None:
        text = _content_text(payload.get("content"))
        if not text:
            return
        conversation_id = self._conversation_id(session_id)
        if conversation_id is None:
            logger.warning("ASP session %s has no local conversation", session_id)
            return
        board = self.bus.board(ChatNotify)
        if board is None:
            return
        board.publish(
            ChatNotify(publisher=self.handle, conversation_id=conversation_id, text=text)
        )

    def _conversation_id(self, session_id: str) -> int | None:
        known = self._conversations.get(session_id)
        if known is not None:
            return known
        board = self.bus.board(CreateConversationJob)
        if board is None:
            return None
        job_id = board.publish(
            CreateConversationJob(
                publisher=self.handle,
                channel="asp",
                delivery_address=session_id,
            )
        )
        result = _job_result(board, job_id)
        if result is None or result.status is not JobStatus.COMPLETED:
            return None
        conversation_id = result.conversation_id
        if conversation_id is None:
            return None
        self._conversations[session_id] = conversation_id
        self._sessions[conversation_id] = session_id
        return conversation_id

    async def _pump_delivery(self, ready: asyncio.Event) -> None:
        await ready.wait()
        board = self.bus.board(DeliveryNotify)
        if board is None:
            return
        while True:
            job = await asyncio.to_thread(board.claim)
            if job is None:
                await asyncio.sleep(0.25)
                continue
            session_id = self._sessions.get(job.conversation_id) if job.conversation_id else None
            if not session_id or not job.text:
                await asyncio.to_thread(
                    board.submit_result,
                    DeliveryNotifyResult(
                        id=job.id,
                        status=JobStatus.FAILED,
                        error="no ASP session for this conversation",
                    ),
                )
                continue
            try:
                await self.asp.send(session_id, job.text)
            except Exception as error:
                await asyncio.to_thread(
                    board.submit_result,
                    DeliveryNotifyResult(
                        id=job.id,
                        status=JobStatus.FAILED,
                        error=str(error).strip() or type(error).__name__,
                    ),
                )
                continue
            await asyncio.to_thread(board.submit_result, DeliveryNotifyResult(id=job.id))

    @staticmethod
    def _detach_workers(workers: dict[str, BaseWorker]) -> None:
        for worker in reversed(tuple(workers.values())):
            worker.detach()


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return ""


def _job_result(board, job_id: int):
    deadline = time.monotonic() + _RESULT_TIMEOUT
    while time.monotonic() < deadline:
        result = board.get_result(job_id)
        if result is not None:
            return result
        time.sleep(0.01)
    return None
