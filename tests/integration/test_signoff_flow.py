"""Integration tests for the tag-based ``hook_signoffs`` flow.

These verify the new design:

  - ``bus.store.enqueue_*`` dispatches one signoff per enabled
    plugin subscribed to the matching hook point.
  - Downstream ``claim_next_*`` filters rows that still have a
    pending signoff for the subject.
  - ``bus.store.ack_signoff`` flips the pending flag so the next
    claim can see the row.
  - Plugins that are not enabled (``hook_plugin_configs.enabled
    = 0``) do not generate signoffs.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from magi.bus.bootstrap import get_bus_store
from magi.bus.db import init_orm


@pytest.fixture
def fresh_bus(monkeypatch, tmp_path: Path):
    """Stand up a clean BUS singleton + SQLite per test.

    The fixture sets the launcher state path so both
    ``init_orm`` and ``get_bus_store`` resolve to the same DB
    file.  No explicit state_dir arg is passed to ``init_orm``
    so it follows ``launcher.paths.state_dir()`` -- the same
    path the BusStore uses.
    """
    from magi.launcher.paths import state_dir as launcher_state_dir

    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("MAGI_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HOST_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("MAGIS_DATABASE_URL", f"sqlite:///{tmp_path / 'magis.db'}")
    _bm = importlib.import_module("magi.bus.bootstrap")
    import magi.bus.db.engine as _engine_mod
    _bm._bus = None
    _engine_mod._engine = None
    init_orm(seed_root=False)
    yield str(launcher_state_dir())


def _install_plugin(state_dir: str, *, plugin_id: str, hook_points: list[str]) -> None:
    """Insert one row in ``hook_plugin_configs``."""
    from sqlalchemy import text
    from magi.bus.db.engine import open_session

    with open_session(state_dir) as session:
        session.execute(
            text(
                "INSERT INTO hook_plugin_configs "
                "(hook_id, hook_version, module_path, class_name, "
                " enabled, mode, priority, required_scopes, timeout_ms, "
                " failure_mode, hook_points, init_kwargs_json, "
                " created_at, updated_at) "
                "VALUES (:id, '1', 'magi.plugins.samples.audit_log', "
                " 'AuditLogPlugin', 1, 'observe', 100, '[]', 500, "
                " 'fail_open', :hps, '{}', '2026-01-01 00:00:00', "
                " '2026-01-01 00:00:00')"
            ),
            {"id": plugin_id, "hps": str(hook_points).replace("'", '"')},
        )
        session.commit()


def test_enqueue_dispatches_signoff_for_subscribed_plugin(fresh_bus):
    """bus.store.enqueue_llm_job stamps one signoff per subscribed plugin."""
    state_dir = fresh_bus
    store = get_bus_store()
    _install_plugin(
        state_dir,
        plugin_id="audit_log",
        hook_points=["llm.request.prepared"],
    )
    attempt_id = store.enqueue_llm_job(
        run_id="r-1", inbox_event_id=None, kind="chat",
    )
    # Plugin worker pulls its signoff.
    signoffs = store.claim_pending_signoffs("audit_log")
    assert len(signoffs) == 1
    assert signoffs[0].subject_type == "llm_attempt"
    assert signoffs[0].subject_id == attempt_id
    assert signoffs[0].hook_point == "llm.request.prepared"
    assert signoffs[0].plugin_id == "audit_log"
    # Re-claim returns nothing -- the row is already leased.
    assert store.claim_pending_signoffs("audit_log") == []
    # Provider worker cannot claim the row while the signoff is pending.
    assert store.claim_next_llm_job("provider-1") is None
    # After ack, the row is claimable.
    store.ack_signoff(signoffs[0].id)
    claimed = store.claim_next_llm_job("provider-1")
    assert claimed is not None
    assert claimed[0] == attempt_id


def test_disabled_plugin_does_not_generate_signoff(fresh_bus):
    """An enabled=0 row in hook_plugin_configs is ignored by the dispatcher."""
    from sqlalchemy import text
    from magi.bus.db.engine import open_session

    state_dir = fresh_bus
    store = get_bus_store()
    with open_session(state_dir) as session:
        session.execute(
            text(
                "INSERT INTO hook_plugin_configs "
                "(hook_id, hook_version, module_path, class_name, "
                " enabled, mode, priority, required_scopes, timeout_ms, "
                " failure_mode, hook_points, init_kwargs_json, "
                " created_at, updated_at) "
                "VALUES ('noop', '1', 'x', 'X', 0, 'observe', 100, "
                " '[]', 500, 'fail_open', '[\"llm.request.prepared\"]', "
                " '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        session.commit()

    store.enqueue_llm_job(
        run_id="r-2", inbox_event_id=None, kind="chat",
    )
    # No signoff rows; the plugin is disabled.
    assert store.claim_pending_signoffs("noop") == []
    # Provider worker can claim right away (no pending signoffs).
    assert store.claim_next_llm_job("provider-2") is not None


def test_complete_dispatches_observation_signoff(fresh_bus):
    """bus.store.complete_llm_attempt fires LLM_RESPONSE_RECEIVED."""
    state_dir = fresh_bus
    store = get_bus_store()
    _install_plugin(
        state_dir,
        plugin_id="audit_log",
        hook_points=["llm.response.received"],
    )
    attempt_id = store.enqueue_llm_job(
        run_id="r-3", inbox_event_id=None, kind="chat",
    )
    # No response-received signoff yet -- the row is only queued.
    assert store.claim_pending_signoffs("audit_log") == []
    # Complete the row -> signoff is stamped.
    store.complete_llm_attempt(attempt_id, response={"text": "ok"})
    signoffs = store.claim_pending_signoffs("audit_log")
    assert len(signoffs) == 1
    assert signoffs[0].hook_point == "llm.response.received"
    assert signoffs[0].subject_id == attempt_id


def test_enqueue_dispatch_is_idempotent(fresh_bus):
    """Two enqueues for the same attempt_id dispatch one signoff, not two."""
    state_dir = fresh_bus
    store = get_bus_store()
    _install_plugin(
        state_dir,
        plugin_id="audit_log",
        hook_points=["llm.request.prepared"],
    )
    attempt_id = store.enqueue_llm_job(
        run_id="r-4", inbox_event_id=None, kind="chat",
    )
    # Manually re-dispatch (simulates a retry path).
    from magi.bus.store import _dispatch_hook_signoffs
    _dispatch_hook_signoffs(
        state_dir=state_dir,
        subject_type="llm_attempt",
        subject_id=attempt_id,
        hook_point="llm.request.prepared",
    )
    signoffs = store.claim_pending_signoffs("audit_log")
    # INSERT OR IGNORE -> unique constraint keeps it to one row.
    assert len(signoffs) == 1


def test_tool_lifecycle_full_signoff_round_trip(fresh_bus):
    """TOOL_CALL_PENDING then ack, then TOOL_RESULT_RECEIVED, then ack."""
    state_dir = fresh_bus
    store = get_bus_store()
    _install_plugin(
        state_dir,
        plugin_id="audit_log",
        hook_points=[
            "tool.call.pending",
            "tool.result.received",
        ],
    )
    # enqueue_tool_job -> TOOL_CALL_PENDING signoff.
    job_id = store.enqueue_tool_job(
        run_id="r-5",
        tool_call_id="call-5",
        tool_name="echo",
        arguments={"text": "hi"},
        context={},
    )
    assert store.claim_next_tool_job("tool-worker-1") is None
    pending = store.claim_pending_signoffs("audit_log")
    assert len(pending) == 1
    assert pending[0].hook_point == "tool.call.pending"
    # Ack; tool worker can claim.
    store.ack_signoff(pending[0].id)
    claim = store.claim_next_tool_job("tool-worker-1")
    assert claim is not None
    assert claim.job_id == job_id
    # Complete the tool -> TOOL_RESULT_RECEIVED signoff.
    from magi.bus.protocols.tools import ToolClaim
    store.complete_tool_job(
        claim,
        content="echo:hi",
        is_error=False,
    )
    observed = store.claim_pending_signoffs("audit_log")
    assert len(observed) == 1
    assert observed[0].hook_point == "tool.result.received"


def test_delivery_lifecycle_full_signoff_round_trip(fresh_bus):
    """DELIVERY_PENDING then ack, then DELIVERY_DISPATCHED, then ack."""
    state_dir = fresh_bus
    store = get_bus_store()
    _install_plugin(
        state_dir,
        plugin_id="audit_log",
        hook_points=[
            "delivery.pending",
            "delivery.dispatched",
        ],
    )
    delivery_id = store.enqueue_delivery(
        channel="tg",
        destination="@target",
        payload={"text": "hello"},
    )
    # Cannot claim while signoff pending.
    assert store.claim_next_delivery("delivery-1") is None
    pending = store.claim_pending_signoffs("audit_log")
    assert len(pending) == 1
    assert pending[0].hook_point == "delivery.pending"
    # Ack.
    store.ack_signoff(pending[0].id)
    claim = store.claim_next_delivery("delivery-1")
    assert claim is not None
    assert claim.delivery_id == delivery_id
    # Complete -> DELIVERY_DISPATCHED signoff.
    store.complete_delivery(delivery_id)
    observed = store.claim_pending_signoffs("audit_log")
    assert len(observed) == 1
    assert observed[0].hook_point == "delivery.dispatched"