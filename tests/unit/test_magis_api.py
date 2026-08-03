"""MAGIS role persistence rules."""
from sqlalchemy import select


def test_each_magis_gets_reserved_roles(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    from magi.db import MAGIS, MAGISRole, init_orm, open_session
    from magi.bus.models.magis.magis_membership import ensure_default_roles
    init_orm(str(tmp_path))
    with open_session() as db:
        society = MAGIS(name="Research", parent_id=None)
        db.add(society); db.flush(); ensure_default_roles(db, society.id); db.commit()
        roles = db.scalars(select(MAGISRole).where(MAGISRole.magis_id == society.id)).all()
        assert {(r.name, r.is_reserved) for r in roles} == {("Adam", True), ("EVE", True)}


def test_adam_does_not_manage_descendant_without_direct_membership(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    from magi.db import MAGIC, MAGIS, MAGISMembership, init_orm, open_session
    from magi.bus.models.magis.magis_membership import adam_manages_magis, ensure_default_roles
    init_orm(str(tmp_path), seed_root=False)
    with open_session() as db:
        root, child, adam = MAGIS(name="Root"), MAGIS(name="Child", parent_id=None), MAGIC(name="Adam")
        db.add_all([root, child, adam]); db.flush(); child.parent_id = root.id
        roles = ensure_default_roles(db, root.id)
        db.add(MAGISMembership(magis_id=root.id, magic_id=adam.id, role_id=roles["Adam"].id)); root.adam_id = adam.id; db.commit()
        assert not adam_manages_magis(db, adam.id, child.id)
        assert db.scalar(select(MAGISMembership).where(MAGISMembership.magic_id == adam.id, MAGISMembership.magis_id == child.id)) is None
