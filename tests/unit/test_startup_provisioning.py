"""Regression coverage for explicit MAGI provisioning and runtime opening."""

from __future__ import annotations

from pathlib import Path

import pytest

from magi.bus import open_bus
from magi.bus.provision import StorageNotProvisioned
from magi.startup.config import DEFAULT_MAGI_NAME, ConfigurationError, StartupConfig
from magi.startup.provision import create_node, init_first_magi
from magi.startup.spec import load_runtime_spec


def _first_config(root: Path) -> StartupConfig:
    return StartupConfig(root, DEFAULT_MAGI_NAME, None, None)


def test_init_provisions_only_canonical_node_database(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)

    workspace = config.workspace_dir
    assert spec.runtime_port == 42070
    assert (workspace / "memories" / "magi.db").is_file()
    assert not (workspace / "magi.db").exists()
    assert (tmp_path / "MAGI_Societies" / "genesis" / "magis.db").is_file()
    assert load_runtime_spec(workspace) == spec
    assert open_bus(
        state_dir=str(workspace / "memories"), magis_url=spec.magis_database_url,
    ).settings_book.get(key="auth.signing_key")


def test_node_creation_has_sticky_distinct_runtime_port(tmp_path: Path) -> None:
    first = init_first_magi(_first_config(tmp_path))
    second = create_node(StartupConfig(tmp_path, "eva-001", None, None))

    assert first.runtime_port == 42070
    assert second.runtime_port == 42071
    assert load_runtime_spec(tmp_path / "MAGI_Citizens" / "eva-001") == second


def test_retired_database_blocks_provision_and_runtime_open(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    config.workspace_dir.mkdir(parents=True)
    (config.workspace_dir / "magi.db").touch()

    with pytest.raises(StorageNotProvisioned, match="retired node database"):
        init_first_magi(config)
    with pytest.raises(ConfigurationError, match="retired node database"):
        load_runtime_spec(config.workspace_dir)
