"""MAGIS role persistence rules."""
from sqlalchemy import select


def test_each_magis_gets_reserved_roles(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.agent.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    from magi.agent.db import MAGIS, MAGISRole, init_orm, open_session
    from magi.agent.db.models_magis_membership import ensure_default_roles
    init_orm(str(tmp_path))
    with open_session() as db:
        society = MAGIS(name="Research", parent_id=None)
        db.add(society); db.flush(); ensure_default_roles(db, society.id); db.commit()
        roles = db.scalars(select(MAGISRole).where(MAGISRole.magis_id == society.id)).all()
        assert {(r.name, r.is_reserved) for r in roles} == {("Adam", True), ("EVE", True)}
