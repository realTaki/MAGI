"""Bus service: delivery (outbox for committed channel delivery effects)."""

from __future__ import annotations

import logging
import time as _time
from typing import Any

from magi.bus.protocols.agent import DeliveryClaim
from magi.bus.store import BusStore

logger = logging.getLogger("magi.bus.service.delivery")


class DeliveryService:
    """Enqueue, lease, and complete committed delivery effects.

    The boundary methods fire BUS hooks (DELIVERY_PENDING GATE on
    enqueue, DELIVERY_DISPATCHED OBSERVE on complete).  The
    :meth:`enqueue_and_wait` helper is the synchronous path used by
    :func:`magi.channels.dispatcher.send_to_uid` for time-critical
    sends (Telegram auth codes) where the caller cannot wait for
    the delivery worker to pick up the row.
    """

    def __init__(self, store: BusStore) -> None:
        self._store = store

    def enqueue(self, *, hook_context: Any | None = None, **kwargs: Any) -> Any:
        return self._store.enqueue_delivery(hook_context=hook_context, **kwargs)

    def claim_next(self, worker_id: str, *, lease_seconds: int = 60) -> DeliveryClaim | None:
        return self._store.claim_next_delivery(worker_id, lease_seconds=lease_seconds)

    def complete(
        self,
        delivery_id: str,
        *,
        hook_context: Any | None = None,
    ) -> None:
        self._store.complete_delivery(delivery_id, hook_context=hook_context)

    def retry(self, delivery_id: str, *, delay_seconds: int | None = None) -> None:
        self._store.retry_delivery(delivery_id, delay_seconds=delay_seconds)

    def enqueue_and_wait(
        self,
        *,
        channel: str,
        destination: str | None,
        payload: dict[str, Any],
        run_id: str | None = None,
        hook_context: Any | None = None,
        timeout_seconds: float = 8.0,
        poll_seconds: float = 0.05,
    ) -> bool:
        """Enqueue a delivery and block until the worker delivers it.

        Returns ``True`` if the row reached ``delivered`` status
        before the timeout, ``False`` otherwise.  Used by the
        channel dispatcher for paths that need a synchronous "send
        then continue" semantics (e.g. Telegram auth codes the
        user is waiting on).
        """
        result = self.enqueue(
            channel=channel,
            destination=destination,
            payload=payload,
            run_id=run_id,
            hook_context=hook_context,
        )
        delivery_id = getattr(result, "row_id", result)
        deadline = _time.monotonic() + max(0.0, timeout_seconds)
        while _time.monotonic() <= deadline:
            status = self._read_delivery_status(delivery_id)
            if status == "delivered":
                return True
            if status in {"dead", "failed"}:
                # The delivery exhausted its retry budget.  Surface
                # the failure to the caller as a False result.
                return False
            _time.sleep(poll_seconds)
        return False

    def _read_delivery_status(self, delivery_id: str) -> str | None:
        """Read the current row status; ``None`` if the row is missing."""
        from sqlalchemy import select

        from magi.bus.db.engine import open_session
        from magi.bus.models.queue import DeliveryOutbox

        with open_session(self._store._state_dir) as session:  # noqa: SLF001
            row = session.scalar(
                select(DeliveryOutbox).where(DeliveryOutbox.delivery_id == delivery_id)
            )
            return row.status if row is not None else None
