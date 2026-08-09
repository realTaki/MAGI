"""EVA runtime identity must not create a second Genesis ADAM."""

from __future__ import annotations


def test_node_config_rejects_non_integer_runtime_id(monkeypatch):
    from magi.__main__ import NodeConfig

    monkeypatch.setenv("MAGI_RUNTIME_ID", "not-a-number")
    try:
        NodeConfig.from_env()
    except ValueError as exc:
        assert "MAGI_RUNTIME_ID" in str(exc)
    else:
        raise AssertionError("a non-integer MAGI_RUNTIME_ID must be rejected")


def test_node_config_without_runtime_id_is_genesis(monkeypatch):
    from magi.__main__ import NodeConfig

    monkeypatch.delenv("MAGI_RUNTIME_ID", raising=False)
    cfg = NodeConfig.from_env()
    assert cfg.is_genesis is True
    assert cfg.runtime_id is None


def test_node_config_with_runtime_id_is_not_genesis(monkeypatch):
    from magi.__main__ import NodeConfig

    monkeypatch.setenv("MAGI_RUNTIME_ID", "42")
    cfg = NodeConfig.from_env()
    assert cfg.is_genesis is False
    assert cfg.runtime_id == "42"
