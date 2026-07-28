"""EVE runtime identity must not create a second Genesis Adam."""

from __future__ import annotations


def test_eve_node_config_requires_runtime_id(monkeypatch):
    from magi.node import NodeConfig

    monkeypatch.setenv("MAGI_NODE_ROLE", "eve")
    monkeypatch.delenv("MAGI_RUNTIME_ID", raising=False)
    try:
        NodeConfig.from_env()
    except ValueError as exc:
        assert "MAGI_RUNTIME_ID" in str(exc)
    else:
        raise AssertionError("an EVE without a runtime identity must be rejected")


def test_eve_provider_uses_injected_secret(monkeypatch):
    from magi.agent.llm.factory import get_provider

    monkeypatch.setenv("MAGI_LLM_PROVIDER", "claude")
    monkeypatch.setenv("MAGI_LLM_API_KEY", "secret-from-kubernetes")
    provider = get_provider()
    assert provider.api_key == "secret-from-kubernetes"
