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

from pathlib import Path

import pytest

from magi.db import init_orm, open_session


def test_canonical_head_equals_real_head() -> None:
    """``alembic_runner.CANONICAL_HEAD`` must be the terminal head.

    This is the static guard. If a future revision is added but the
    constant is not bumped, ``upgrade_head`` will silently leave the
    new migration un-applied on every boot — the same class of bug
    P0.1 fixed.
    """
    from magi.db.alembic_runner import CANONICAL_HEAD, _find_alembic_ini
    from magi.db.engine import _state_dir_from_env
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(_find_alembic_ini()))
    config.set_main_option(
        "script_location",
        str(Path(_find_alembic_ini()).parent / "alembic"),
    )
    # Set a dummy sqlalchemy.url so Config doesn't choke on an unset URL.
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{Path(_state_dir_from_env()) / 'magi.db'}",
    )
    heads = set(ScriptDirectory.from_config(config).get_heads())
    assert CANONICAL_HEAD in heads, (
        f"CANONICAL_HEAD={CANONICAL_HEAD!r} is not a terminal revision; "
        f"real heads are {sorted(heads)}. Update the constant."
    )


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

    # Bootstrap at 0005 (the pre-fix head). The simplest way to land there
    # is to upgrade just to 0005 and stop — alembic's normal upgrade
    # would race past it on a fresh DB.
    from alembic.command import upgrade as alembic_upgrade
    from magi.db.alembic_runner import _find_alembic_ini, _config_for_state_dir

    config = _config_for_state_dir(tmp_path)
    alembic_upgrade(config, "0005_agent_bus")

    # Confirm we are at 0005 before running the full upgrade.
    with open_session() as db:
        row = db.execute(
            __import__("sqlalchemy").text(
                "SELECT version_num FROM alembic_version"
            )
        ).first()
    assert row is not None and row[0] == "0005_agent_bus"

    # Now run init_orm — this is what production boot does — which calls
    # upgrade_head internally. With the fix it must walk 0006 → 0007 →
    # 0008 and apply every column.
    init_orm(str(tmp_path), seed_root=False)

    with open_session() as db:
        heads = db.execute(
            __import__("sqlalchemy").text(
                "SELECT version_num FROM alembic_version"
            )
        ).first()
    assert heads is not None and heads[0] == "0008_merge_actor_and_auth_heads"

    # Spot-check that the critical columns added by 0006 and 0007 exist.
    # If any of these fail, the runtime would have crashed at first ORM
    # query against the column.
    with open_session() as db:
        cols = {
            row[1]
            for row in db.execute(
                __import__("sqlalchemy").text("PRAGMA table_info(agent_inbox)")
            ).fetchall()
        }
    assert "conversation_id" in cols  # added by 0006
    assert "correlation_id" in cols
    assert "causation_id" in cols

    with open_session() as db:
        cols = {
            row[1]
            for row in db.execute(
                __import__("sqlalchemy").text("PRAGMA table_info(run_inputs)")
            ).fetchall()
        }
    assert "received_seq" in cols  # added by 0006
    assert "context_seq" in cols
    assert "status" in cols

    with open_session() as db:
        cols = {
            row[1]
            for row in db.execute(
                __import__("sqlalchemy").text("PRAGMA table_info(llm_attempts)")
            ).fetchall()
        }
    assert "inbox_event_id" in cols  # added by 0006
    assert "provider" in cols
    assert "model" in cols
    assert "last_stream_seq" in cols

    with open_session() as db:
        cols = {
            row[1]
            for row in db.execute(
                __import__("sqlalchemy").text("PRAGMA table_info(chat_messages)")
            ).fetchall()
        }
    assert "content_blocks" in cols  # added by 0007
    assert "run_id" in cols
    assert "llm_attempt_id" in cols

    with open_session() as db:
        cols = {
            row[1]
            for row in db.execute(
                __import__("sqlalchemy").text("PRAGMA table_info(a2a_invocations)")
            ).fetchall()
        }
    assert "tool_call_id" in cols  # added by 0007
    assert "request_event_id" in cols
    assert "reply_to" in cols
    assert "expect_reply" in cols
    assert "deadline_at" in cols
    assert "idempotency_key" in cols


def test_fresh_db_stamps_at_terminal_head(monkeypatch, tmp_path: Path) -> None:
    """A clean init_orm must end at 0008, not at 0005."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    init_orm(str(tmp_path), seed_root=False)

    with open_session() as db:
        row = db.execute(
            __import__("sqlalchemy").text(
                "SELECT version_num FROM alembic_version"
            )
        ).first()
    assert row is not None and row[0] == "0008_merge_actor_and_auth_heads"