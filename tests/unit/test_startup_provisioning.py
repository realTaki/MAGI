"""Regression coverage for explicit MAGI provisioning and runtime opening."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from magi.bus import open_bus, open_control_bus
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


def test_repeated_init_is_identity_and_key_idempotent(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    first = init_first_magi(config)
    first_bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"), magis_url=first.magis_database_url,
    )
    signing_key = first_bus.settings_book.get(key="auth.signing_key")
    members_before = first_bus.memberships_book.list_for_magis(magis_id=1)

    second = init_first_magi(config)
    second_bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"), magis_url=second.magis_database_url,
    )

    assert second == first
    assert second_bus.settings_book.get(key="auth.signing_key") == signing_key
    assert second_bus.memberships_book.list_for_magis(magis_id=1) == members_before


def test_repeated_node_create_fails_without_duplicate_registration(tmp_path: Path) -> None:
    init_first_magi(_first_config(tmp_path))
    config = StartupConfig(tmp_path, "eva-001", None, None)
    create_node(config)

    with pytest.raises(ConfigurationError, match="workspace already exists"):
        create_node(config)


def test_retired_database_blocks_provision_and_runtime_open(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    config.workspace_dir.mkdir(parents=True)
    (config.workspace_dir / "magi.db").touch()

    with pytest.raises(StorageNotProvisioned, match="retired node database"):
        init_first_magi(config)
    with pytest.raises(ConfigurationError, match="retired node database"):
        load_runtime_spec(config.workspace_dir)


def test_runtime_open_never_creates_a_missing_node_database(tmp_path: Path) -> None:
    state_dir = tmp_path / "MAGI_Citizens" / "eva-000" / "memories"
    state_dir.mkdir(parents=True)

    with pytest.raises(StorageNotProvisioned, match="node database is missing"):
        open_bus(state_dir=str(state_dir))

    assert not (state_dir / "magi.db").exists()


def test_control_bus_uses_magis_store_without_opening_node_store(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    control_dir = tmp_path / "MAGI_Societies" / "genesis" / "control"
    node_database = config.workspace_dir / "memories" / "magi.db"
    before = node_database.stat().st_mtime_ns

    bus = open_control_bus(control_dir=str(control_dir), magis_url=spec.magis_database_url)

    assert bus._local_factory.url == spec.magis_database_url
    assert bus._magis_factory is not None
    assert not control_dir.exists()
    assert node_database.stat().st_mtime_ns == before


def test_runtime_open_rejects_an_outdated_schema_without_migrating(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"), magis_url=spec.magis_database_url,
    )
    with bus._local_factory.engine.begin() as connection:
        connection.execute(
            text("UPDATE magi_schema_revisions SET revision = 'stale' WHERE scope = 'node'")
        )

    with pytest.raises(StorageNotProvisioned, match="expected '0001'"):
        open_bus(state_dir=str(config.workspace_dir / "memories"), magis_url=spec.magis_database_url)
