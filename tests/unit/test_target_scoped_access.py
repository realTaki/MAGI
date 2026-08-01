"""Regression coverage for MAGIS-scoped browser access."""

from __future__ import annotations

from sqlalchemy import select


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.db.engine as engine_mod

    engine_mod._engine = engine_mod._SessionLocal = None
    from magi.db import init_orm

    init_orm(str(tmp_path))


def test_target_login_accounts_union_direct_magis_admin_and_local_assignee(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    from magi.db import Contact, MAGIC, MAGIS, MAGISAdmin, open_session
    from magi.channels.webui.api.runtime_access import _accounts

    with open_session() as db:
        root = db.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)))
        adam = db.get(MAGIC, root.adam_id)
        db.add(MAGISAdmin(magis_id=root.id, telegram_id=1001, display_name="Society admin"))
        db.add(Contact(name="Assigned", role="assigned", telegram_id=2002))
        db.commit()

        accounts = _accounts(root.id)
        assert accounts[1001].admin and not accounts[1001].assigned
        assert accounts[2002].assigned and not accounts[2002].admin
        assert adam is not None


def test_selected_session_is_bound_to_one_magic(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CONTROL_SECRET", "test-secret")
    from magi.channels.webui.api.auth import _sign_selected_session, selected_session

    token = _sign_selected_session(
        magic_id=7, telegram_id=1001, display_name="Admin", admin=True, assigned=False,
    )
    assert selected_session(token) == {
        "v": 2,
        "magic_id": 7,
        "telegram_id": 1001,
        "display_name": "Admin",
        "admin": True,
        "assigned": False,
        "ts": selected_session(token)["ts"],
    }
