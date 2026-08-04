"""Regression coverage for producer-supplied idempotency keys.

These tests guard the P1.2 fix: ``agent_inbox``, ``tool_jobs``,
``delivery_outbox`` and ``run_inputs`` honour producer-supplied
idempotency boundaries. The contract is "at-least-once delivery +
idempotent consumption": producers may re-publish, but the bus
collapses duplicates to the same durable row.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from magi.bus import AgentMessage, BusStore
from magi.bus.models.queue import AgentInbox, DeliveryOutbox, ToolJob
from magi.bus.contracts.agent import A2AInvocationRequest
from magi.bus.db import (
    init_orm,
    open_session,
)


@pytest.fixture()
def store(tmp_path, monkeypatch) -> BusStore:
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    init_orm(str(tmp_path / "memories"), seed_root=False)
    return BusStore(str(tmp_path))


def _raw_columns(db_path: Path, table: str) -> set[str]:
    raw = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in raw.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()}
    finally:
        raw.close()


def test_migration_0009_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    """Running init_orm twice in a row is a no-op on the second call.

    The 0009 migration must use ``_columns`` / ``_indexes`` inspectors
    so a DB that already has the new columns is left untouched. Without
    this, every boot would re-attempt ``ADD COLUMN`` / ``CREATE INDEX``
    and crash on the second one.
    """
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    import magi.bus.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    init_orm(str(tmp_path / "memories"), seed_root=False)
    # Reset engine cache so the second init_orm sees a "fresh" environment
    # (init_orm is idempotent at the alembic level via version stamp,
    # but the cache means the second call would be a no-op anyway).
    engine_mod._engine = engine_mod._SessionLocal = None
    init_orm(str(tmp_path / "memories"), seed_root=False)

    # If the second upgrade tried to re-add columns the migration would
    # have raised; reaching here is success. Spot-check the new
    # columns are present.
    cols = _raw_columns(tmp_path / "magi.db", "agent_inbox")
    assert "source_type" in cols
    assert "external_event_id" in cols


def test_agent_inbox_partial_unique_blocks_duplicate_external_event_id(
    store: BusStore,
) -> None:
    """Two AgentMessages with the same cross-channel triple collapse.

    Different ``event_id`` values but the same
    ``(source_type, source_id, external_event_id)`` must return the
    same ``run_id``. This is the TG webhook-redelivery case: a
    bot-restart re-runs the same update with a fresh envelope but the
    upstream update_id is stable.
    """
    first = store.publish_agent_message(AgentMessage(
        event_id="local-1",
        text="hello",
        channel="tg",
        source_type="tg",
        source_id="42",
        external_event_id="upstream-update-1",
    ))
    second = store.publish_agent_message(AgentMessage(
        event_id="local-2",  # different envelope id
        text="hello again",
        channel="tg",
        source_type="tg",
        source_id="42",
        external_event_id="upstream-update-1",  # same upstream id
    ))
    assert first == second

    # Only one row exists, not two.
    with open_session() as session:
        rows = session.query(AgentInbox).filter(
            AgentInbox.external_event_id == "upstream-update-1"
        ).all()
    assert len(rows) == 1


def test_agent_inbox_partial_unique_ignores_rows_without_external_event_id(
    store: BusStore,
) -> None:
    """Producers that don't supply a triple are unaffected.

    The partial index is ``WHERE external_event_id IS NOT NULL``;
    producers that omit the field continue to use the original
    ``UNIQUE(event_id)`` path.
    """
    for i in range(5):
        store.publish_agent_message(AgentMessage(
            event_id=f"no-triple-{i}",
            text=f"msg-{i}",
            channel="test",
        ))
    with open_session() as session:
        count = session.query(AgentInbox).filter(
            AgentInbox.external_event_id.is_(None)
        ).count()
    assert count == 5


def test_enqueue_tool_job_idempotency_key_blocks_duplicate(store: BusStore) -> None:
    """Same idempotency_key collapses to the same job_id."""
    first = store.enqueue_tool_job(
        run_id="run-1",
        tool_call_id="call-a",
        tool_name="fake_tool",
        arguments={},
        context={},
        idempotency_key="stable-handle",
    )
    second = store.enqueue_tool_job(
        run_id="run-1",
        tool_call_id="call-b",  # different tool_call_id
        tool_name="fake_tool",
        arguments={},
        context={},
        idempotency_key="stable-handle",  # same idempotency_key
    )
    assert first == second

    with open_session() as session:
        count = session.query(ToolJob).filter(
            ToolJob.idempotency_key == "stable-handle"
        ).count()
    assert count == 1


def test_enqueue_delivery_event_id_blocks_duplicate(store: BusStore) -> None:
    """``event_id`` (correlating back to an inbox event) dedupes."""
    first = store.enqueue_delivery(
        channel="tg",
        destination="123",
        payload={"text": "hello"},
        event_id="webui-message-abc",
    )
    second = store.enqueue_delivery(
        channel="tg",
        destination="123",
        payload={"text": "different body"},
        event_id="webui-message-abc",
    )
    assert first == second

    with open_session() as session:
        count = session.query(DeliveryOutbox).filter(
            DeliveryOutbox.event_id == "webui-message-abc"
        ).count()
    assert count == 1


def test_enqueue_delivery_idempotency_key_blocks_duplicate(store: BusStore) -> None:
    """``idempotency_key`` (producer-side handle) dedupes."""
    first = store.enqueue_delivery(
        channel="tg",
        destination="123",
        payload={"text": "hello"},
        idempotency_key="reply:run-xyz",
    )
    second = store.enqueue_delivery(
        channel="tg",
        destination="123",
        payload={"text": "different body"},
        idempotency_key="reply:run-xyz",
    )
    assert first == second

    with open_session() as session:
        count = session.query(DeliveryOutbox).filter(
            DeliveryOutbox.idempotency_key == "reply:run-xyz"
        ).count()
    assert count == 1


def test_commit_agent_transition_writes_idempotency_keys(store: BusStore) -> None:
    """actor transition persists tool_jobs.idempotency_key.

    The agent worker defaults ``idempotency_key`` to
    ``f"tool:{run_id}:{tool_call_id}"`` so a re-emitted transition
    collapses to the same ToolJob row.
    """
    run_id = store.publish_agent_message(AgentMessage(
        event_id="actor-root",
        text="hi",
        channel="test",
    ))
    claim = store.claim_next_agent_message("agent")
    assert claim is not None
    store.commit_agent_transition(
        claim.event_id,
        continuation={"input": claim.payload, "messages": [], "tool_call_ids": ["call-1"]},
        jobs=[{
            "tool_call_id": "call-1",
            "tool_name": "fake_tool",
            "arguments": {},
            "context": {},
        }],
    )
    with open_session() as session:
        job = session.query(ToolJob).filter(ToolJob.tool_call_id == "call-1").one()
    assert job.idempotency_key == f"tool:{run_id}:call-1"


def test_commit_agent_transition_writes_delivery_event_id(
    store: BusStore,
) -> None:
    """The TG reply DeliveryOutbox carries event_id + idempotency_key."""
    run_id = store.publish_agent_message(AgentMessage(
        event_id="tg-reply-root",
        text="hi",
        channel="tg",
    ))
    claim = store.claim_next_agent_message("agent")
    assert claim is not None
    store.complete_agent_message(
        claim.event_id,
        "thanks",
        delivery_destination="42",
    )
    with open_session() as session:
        delivery = session.query(DeliveryOutbox).filter(
            DeliveryOutbox.delivery_id == f"delivery:{run_id}"
        ).one()
    assert delivery.event_id == "tg-reply-root"
    assert delivery.idempotency_key == f"reply:{run_id}"