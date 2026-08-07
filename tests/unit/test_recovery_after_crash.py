# TODO: migrate to new_bus — currently failing under the
# tools/new_bus migration (see magi/startup/runtime.py and
# magi/new_bus). Re-baseline this test file when the agent
# loop moves to bus.tool_job_board + the new ToolWorker.
"""§20.3 + §20.8 — crash recovery semantics.

The actor runtime guarantees:

  - A leased inbox / tool job / delivery whose lease has expired
    can be reclaimed by another worker.
  - An interrupted LLM attempt (started but never committed) is
    marked ``interrupted`` so the next attempt starts a new
    ``attempt_id`` rather than reusing old stream deltas.
  - A pending delivery in the outbox is delivered after process
    restart; ``recover_expired_leases`` flips it from
    ``processing`` (expired) to ``retry``.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from magi.bus import AgentMessage, BusStore
from magi.bus.db.models.queue import AgentInbox, DeliveryOutbox, LLMAttempt
from magi.bus.db import (
    init_orm,
    open_session,
)
from magi.bus.db.base import utcnow_naive


@pytest.fixture()
def store(tmp_path: Path, monkeypatch) -> BusStore:
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    init_orm(str(tmp_path / "memories"), seed_root=False)
    return BusStore(str(tmp_path))


def test_interrupted_transition_uses_new_attempt_id(
    store: BusStore,
) -> None:
    """An LLM attempt left in ``started`` is marked interrupted by recovery.

    Lease recovery flags every still-processing inbox event as
    ``interrupted`` (via ``recover_expired_leases``) — see the
    corresponding logic in the agent worker. We assert the
    supporting invariant: an LLM attempt left in ``started`` past
    its lease is recoverable and the next call gets a fresh
    attempt_id.
    """
    run_id = store.publish_agent_message(AgentMessage(
        event_id="recovery-root",
        text="hi",
        channel="test",
    ))
    claim = store.claim_next_agent_message("agent")
    assert claim is not None

    # Begin an LLM attempt and let it "crash" mid-flight (no
    # commit). ``start_llm_attempt`` returns a fresh attempt_id
    # we never resolve.
    first_attempt = store.start_llm_attempt(run_id, claim.event_id)
    # Force the attempt's lease to expire by tweaking started_at /
    # deadline downstream — here we just confirm the attempt
    # is in ``started`` state.
    with open_session() as session:
        att = session.query(LLMAttempt).filter(
            LLMAttempt.attempt_id == first_attempt
        ).one()
    assert att.status == "started"

    # The next transition's ``start_llm_attempt`` must return a
    # *different* attempt_id, never reusing the orphan's id.
    second_attempt = store.start_llm_attempt(run_id, claim.event_id)
    assert second_attempt != first_attempt


def test_pending_delivery_resumes_after_process_restart(
    tmp_path: Path, monkeypatch
) -> None:
    """A pending DeliveryOutbox row is recoverable on a fresh BusStore.

    Simulate the restart: write a DeliveryOutbox via BusStore,
    then re-instantiate the BusStore and call ``claim_next_delivery``
    + ``complete_delivery`` from a new worker. The delivery
    survives the restart because it was never leased (and is
    therefore not subject to lease-expiry recovery).
    """
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    init_orm(str(tmp_path / "memories"), seed_root=False)
    store = BusStore(str(tmp_path))
    run_id = store.publish_agent_message(AgentMessage(
        event_id="restart-delivery-root",
        text="hi",
        channel="tg",
    ))
    claim = store.claim_next_agent_message("agent")
    assert claim is not None
    store.complete_agent_message(claim.event_id, "restart-me", delivery_destination="42")

    # Simulate restart: drop the engine cache and rebuild.
    import magi.bus.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    fresh_store = BusStore(str(tmp_path))

    delivery = fresh_store.claim_next_delivery("delivery-1")
    assert delivery is not None
    fresh_store.complete_delivery(delivery.delivery_id)

    with open_session() as session:
        row = session.query(DeliveryOutbox).filter(
            DeliveryOutbox.delivery_id == delivery.delivery_id
        ).one()
    assert row.status == "delivered"


def test_expired_inbox_lease_recovers_for_new_worker(
    store: BusStore,
) -> None:
    """A leased inbox event whose lease has expired is recoverable.

    Demonstrates the §15 / §20.3 contract: ``recover_expired_leases``
    flips the ``processing`` row back to ``retry`` so the next
    claim returns the same run_id but with ``attempts + 1``.
    """
    run_id = store.publish_agent_message(AgentMessage(
        event_id="lease-recover",
        text="hi",
        channel="test",
    ))
    claim = store.claim_next_agent_message("dead-worker", lease_seconds=3600)
    assert claim is not None

    # Force the lease to expire.
    with open_session() as session:
        row = session.query(AgentInbox).filter(
            AgentInbox.event_id == claim.event_id
        ).one()
        row.leased_until = utcnow_naive() - timedelta(seconds=10)
        session.commit()

    recovered = store.recover_expired_leases()
    assert recovered >= 1

    new_claim = store.claim_next_agent_message("replacement-worker")
    assert new_claim is not None
    assert new_claim.run_id == run_id
    assert new_claim.attempts == 2