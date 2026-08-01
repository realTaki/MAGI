"""EVE runtime identity must not create a second Genesis Adam."""

from __future__ import annotations


def test_eve_node_config_requires_runtime_id(monkeypatch):
    from magi.__main__ import NodeConfig

    monkeypatch.setenv("MAGI_NODE_ROLE", "eve")
    monkeypatch.delenv("MAGI_RUNTIME_ID", raising=False)
    try:
        NodeConfig.from_env()
    except ValueError as exc:
        assert "MAGI_RUNTIME_ID" in str(exc)
    else:
        raise AssertionError("an EVE without a runtime identity must be rejected")


def test_eve_provider_ignores_legacy_environment_credentials(monkeypatch, tmp_path):
    """Provider config now comes from the direct MAGIS public database."""
    import magi.db.engine as engine_mod
    engine_mod._engine = engine_mod._SessionLocal = None
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("MAGIS_DATABASE_URL", f"sqlite:///{tmp_path / 'public.db'}")
    from magi.db import init_orm
    from magi.db.magis import init_magis_public_db
    init_orm(seed_root=False)
    init_magis_public_db(seed_root=True)
    from magi.agent.llm.factory import get_provider
    from magi.agent.llm.errors import LLMNotConfiguredError

    monkeypatch.setenv("MAGI_LLM_PROVIDER", "claude")
    monkeypatch.setenv("MAGI_LLM_API_KEY", "legacy-secret")
    try:
        get_provider()
    except LLMNotConfiguredError:
        pass
    else:
        raise AssertionError("legacy environment credentials must not configure an EVE")
