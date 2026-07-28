"""Tests for the ORM-backed system settings facade."""

from __future__ import annotations

import pytest
from sqlalchemy import text


def _reset_engine() -> None:
    import magi.agent.db.engine as orm_mod

    orm_mod._engine = None
    orm_mod._SessionLocal = None


@pytest.fixture
def state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    _reset_engine()
    return state


def test_state_helpers_use_the_orm_settings_model(state_dir):
    from magi.agent.db import Setting, init_sqlite, open_session
    from magi.agent.db.settings import state_delete, state_get, state_set

    # The bootstrap no longer creates settings with raw SQL; the ORM creates
    # it when the first settings helper opens a session.
    init_sqlite(str(state_dir))
    state_set(str(state_dir), "system.timezone", "America/Edmonton")

    assert state_get(str(state_dir), "system.timezone") == "America/Edmonton"

    with open_session() as db:
        row = db.get(Setting, "system.timezone")
        assert row is not None
        assert row.value == "America/Edmonton"
        assert row.updated_at

    state_set(str(state_dir), "system.timezone", "UTC")
    assert state_get(str(state_dir), "system.timezone") == "UTC"

    state_delete(str(state_dir), "system.timezone")
    assert state_get(str(state_dir), "system.timezone") is None


def test_settings_table_is_registered_in_orm_metadata(state_dir):
    from magi.agent.db import Setting, init_orm

    init_orm(str(state_dir))

    assert Setting.__table__.name == "settings"
    assert {column.name for column in Setting.__table__.columns} == {
        "key",
        "value",
        "updated_at",
    }


def test_init_orm_records_alembic_head(state_dir):
    from magi.agent.db import init_orm

    engine = init_orm(str(state_dir))

    with engine.connect() as db:
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()

    assert revision == "0003_add_daily_note_kind"
