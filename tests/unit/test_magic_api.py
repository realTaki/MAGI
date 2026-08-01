"""Core assertions for independent MAGI creation and memberships."""
from sqlalchemy import select


def test_fresh_workspace_seeds_default_magic_as_genesis_adam(
    monkeypatch, tmp_path,
):
    """First boot seeds the canonical adam MAGIC on Genesis.

    The exact seeded name is :data:`magi.agent.db.engine._DEFAULT_MAGI_NAME`
    — pinned here so a rename is intentional rather than silent."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.agent.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    from magi.agent.db import MAGIC, MAGIS, MAGISMembership, MAGISRole, init_orm, open_session
    from magi.agent.db.engine import _DEFAULT_MAGI_NAME
    init_orm(str(tmp_path))
    with open_session() as db:
        genesis = db.scalar(select(MAGIS).where(MAGIS.name == "Genesis"))
        adam_magic = db.get(MAGIC, genesis.adam_id)
        assert adam_magic.name == _DEFAULT_MAGI_NAME
        roles = {r.name: r for r in db.scalars(select(MAGISRole).where(MAGISRole.magis_id == genesis.id))}
        assert set(roles) == {"Adam", "EVE"}
        membership = db.scalar(select(MAGISMembership).where(MAGISMembership.magic_id == adam_magic.id))
        assert membership.role_id == roles["Adam"].id


def test_new_magic_is_unassigned(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.agent.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    from magi.agent.db import MAGIC, MAGISMembership, init_orm, open_session
    init_orm(str(tmp_path))
    with open_session() as db:
        worker = MAGIC(name="Worker")
        db.add(worker); db.commit()
        assert db.scalar(select(MAGISMembership).where(MAGISMembership.magic_id == worker.id)) is None


def test_magic_has_only_one_direct_magis_membership(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.agent.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    from sqlalchemy.exc import IntegrityError
    from magi.agent.db import MAGIC, MAGIS, MAGISMembership, init_orm, open_session
    from magi.agent.db.models_magis_membership import ensure_default_roles
    init_orm(str(tmp_path), seed_root=False)
    with open_session() as db:
        one, two, magic = MAGIS(name="One"), MAGIS(name="Two"), MAGIC(name="Only one home")
        db.add_all([one, two, magic]); db.flush()
        roles_one, roles_two = ensure_default_roles(db, one.id), ensure_default_roles(db, two.id)
        db.add(MAGISMembership(magis_id=one.id, magic_id=magic.id, role_id=roles_one["EVE"].id)); db.commit()
        db.add(MAGISMembership(magis_id=two.id, magic_id=magic.id, role_id=roles_two["EVE"].id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("a MAGI must not receive a second direct membership")
