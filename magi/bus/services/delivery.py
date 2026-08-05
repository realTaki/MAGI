"""Bus service: delivery (outbox for committed channel delivery effects)."""

from __future__ import annotations

from typing import Any

from magi.bus.protocols.agent import DeliveryClaim
from magi.bus.store import BusStore


class DeliveryService:
    """Enqueue, lease, and complete committed delivery effects."""

    def __init__(self, store: BusStore) -> None:
        self._store = store

    def enqueue(self, **kwargs: Any) -> str:
        return self._store.enqueue_delivery(**kwargs)

    def claim_next(self, worker_id: str, *, lease_seconds: int = 60) -> DeliveryClaim | None:
        return self._store.claim_next_delivery(worker_id, lease_seconds=lease_seconds)

    def complete(self, delivery_id: str) -> None:
        self._store.complete_delivery(delivery_id)

    def retry(self, delivery_id: str, *, delay_seconds: int | None = None) -> None:
        self._store.retry_delivery(delivery_id, delay_seconds=delay_seconds)
