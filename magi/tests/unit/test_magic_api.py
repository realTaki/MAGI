"""Core assertions for independent MAGI creation and memberships."""
from sqlalchemy import select


def test_fresh_workspace_seeds_alice_as_genesis_adam(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    import magi.agent.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    from magi.agent.db import MAGIC, MAGIS, MAGISMembership, MAGISRole, init_orm, open_session
    init_orm(str(tmp_path))
    with open_session() as db:
        genesis = db.scalar(select(MAGIS).where(MAGIS.name == "Genesis"))
        alice = db.get(MAGIC, genesis.adam_id)
        assert alice.name == "Alice"
        roles = {r.name: r for r in db.scalars(select(MAGISRole).where(MAGISRole.magis_id == genesis.id))}
        assert set(roles) == {"Adam", "EVE"}
        membership = db.scalar(select(MAGISMembership).where(MAGISMembership.magic_id == alice.id))
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
