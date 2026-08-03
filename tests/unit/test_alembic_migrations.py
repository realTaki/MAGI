"""Regression tests for the alembic migration chain.

These tests guard the P0.1 fix: ``CANONICAL_HEAD`` must match the real
terminal revision, and a DB whose ``alembic_version`` row sits at any
earlier head must have every subsequent revision applied by
``upgrade_head``.

Without this test, future refactors that fold migrations back into
earlier revisions (a known dev-mode pattern) can silently break the
runtime: ``_rebase_to_canonical_head`` re-stamps the bookkeeping row
without checking that the schema matches the new code, and a DB whose
``alembic_version`` was pinned to a folded-away revision will boot but
fail every ORM operation that touches a column the migration had added.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from magi.db import init_orm


def test_canonical_head_equals_real_head() -> None:
    """``alembic_runner.CANONICAL_HEAD`` must be the terminal head.

    This is the static guard. If a future revision is added but the
    constant is not bumped, ``upgrade_head`` will silently leave the
    new migration un-applied on every boot — the same class of bug
    P0.1 fixed.
    """
    from magi.db.alembic_runner import CANONICAL_HEAD, _ALEMBIC_SCRIPT_LOCATION
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option(
        "script_location",
        str(_ALEMBIC_SCRIPT_LOCATION),
    )
    heads = set(ScriptDirectory.from_config(config).get_heads())
    assert CANONICAL_HEAD in heads, (
        f"CANONICAL_HEAD={CANONICAL_HEAD!r} is not a terminal revision; "
        f"real heads are {sorted(heads)}. Update the constant."
    )


def _raw_alembic_version(db_path: Path) -> str | None:
    """Read ``alembic_version.version_num`` via a raw sqlite3 connection.

    Bypasses the SQLAlchemy engine so a test can probe the migration
    state without triggering ``init_orm`` to apply outstanding
    migrations.
    """
    raw = sqlite3.connect(str(db_path))
    try:
        row = raw.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row is not None else None
    finally:
        raw.close()


def _columns(db_path: Path, table: str) -> set[str]:
    raw = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in raw.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()}
    finally:
        raw.close()


def test_post_fix_applies_full_chain_on_legacy_db(monkeypatch, tmp_path: Path) -> None:
    """A DB pinned at 0005 must have 0006/0007/0008 schema applied.

    Simulates the broken-deployment scenario: a developer returned from
    a long stretch where ``CANONICAL_HEAD`` was stuck at 0005, leaving
    their DB at ``alembic_version=0005_agent_bus``. With the fix in
    place, the next ``upgrade_head`` must run 0006/0007/0008 and the
    runtime code's ORM metadata must line up.
    """
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))

    # Force the engine cache to rebuild against the tmp_path DB.
    import magi.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None

    from alembic.command import upgrade as alembic_upgrade
    from magi.db.alembic_runner import _config_for_state_dir

    config = _config_for_state_dir(tmp_path)
    alembic_upgrade(config, "0005_agent_bus")

    # Confirm we are at 0005 BEFORE any runtime code touches the DB.
    db_path = tmp_path / "magi.db"
    assert _raw_alembic_version(db_path) == "0005_agent_bus", (
        "pre-fix fixture failed: alembic upgrade to 0005 did not land "
        "where expected"
    )

    # Now run init_orm — this is what production boot does — which calls
    # upgrade_head internally. With the fix it must walk 0006 → 0007 →
    # 0008 → 0009 → 0010 and apply every column.
    init_orm(str(tmp_path), seed_root=False)
    assert _raw_alembic_version(db_path) == "0013_tool_job_catalog_snapshot"

    # Spot-check that the critical columns added by 0006/0007/0009 exist.
    # If any of these fail, the runtime would have crashed at first ORM
    # query against the column.
    inbox = _columns(db_path, "agent_inbox")
    assert "conversation_id" in inbox  # added by 0006
    assert "correlation_id" in inbox
    assert "causation_id" in inbox
    assert "source_type" in inbox  # added by 0009
    assert "external_event_id" in inbox

    run_inputs = _columns(db_path, "run_inputs")
    assert "received_seq" in run_inputs  # added by 0006
    assert "context_seq" in run_inputs
    assert "status" in run_inputs
    assert "source_event_id" in run_inputs  # added by 0009

    llm = _columns(db_path, "llm_attempts")
    assert "inbox_event_id" in llm  # added by 0006
    assert "provider" in llm
    assert "model" in llm
    assert "last_stream_seq" in llm

    chat = _columns(db_path, "chat_messages")
    assert "content_blocks" in chat  # added by 0007
    assert "run_id" in chat
    assert "llm_attempt_id" in chat

    a2a = _columns(db_path, "a2a_invocations")
    assert "tool_call_id" in a2a  # added by 0007
    assert "request_event_id" in a2a
    assert "reply_to" in a2a
    assert "expect_reply" in a2a
    assert "deadline_at" in a2a
    assert "idempotency_key" in a2a

    tool_jobs = _columns(db_path, "tool_jobs")
    assert "idempotency_key" in tool_jobs  # added by 0009

    delivery_outbox = _columns(db_path, "delivery_outbox")
    assert "event_id" in delivery_outbox  # added by 0009
    assert "idempotency_key" in delivery_outbox

    tool_calls = _columns(db_path, "tool_calls")
    assert "ordinal" in tool_calls  # added by 0010
    assert "ordered_at" in tool_calls

    agent_runs = _columns(db_path, "agent_runs")
    assert "expected_tool_call_ids" in agent_runs  # added by 0011
    assert "expected_a2a_invocation_ids" in agent_runs
    assert "iteration_count" in agent_runs
    assert "token_usage" in agent_runs
    assert "deadline_at" in agent_runs


def test_fresh_db_stamps_at_terminal_head(monkeypatch, tmp_path: Path) -> None:
    """A clean init_orm must end at 0011, not at 0005."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    init_orm(str(tmp_path), seed_root=False)

    assert _raw_alembic_version(tmp_path / "magi.db") == "0013_tool_job_catalog_snapshot"
