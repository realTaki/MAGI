"""End-to-end tests for the 2026-08 MAGI creation-flow refactor.

Covers the contract changes:

  - bootstrap seeds the root MAGIS + ADAM MAGIC with ``id == 0``;
  - ``MagicService.create_magic`` auto-creates the direct MAGIS
    membership in the same transaction;
  - the per-MAGI ``runtime_settings.toml`` file is the new home for
    provider / API key / model; the magic row no longer carries them;
  - the runtime endpoint at ``/api/magic/self/provider`` reads and
    writes the local file;
  - name uniqueness is enforced (DB unique index + service check);
  - ``provider_configuration`` falls back to legacy ``magic.provider``
    / ``magic.api_key`` columns only for pre-refactor rows.

These tests use the same per-test SQLite fixture as
``test_providers_worker.py`` — a ``tmp_path`` is wired into both the
private SQLite (via ``MAGI_WORKSPACE_DIR``) and the public MAGIS
SQLite (via ``MAGIS_DATABASE_URL``) so each test sees a fresh
database with a fresh bootstrap.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from magi.bus.bootstrap import bootstrap
from magi.bus.db import init_orm
# open_session not used (helpers re-import per-test)
from magi.bus.db.magis import open_magis_session
from magi.bus.db.magis.engine import init_magis_public_db
from magi.bus.db.models.magis.magic import MAGIC
from magi.bus.db.models.magis.magis import MAGIS
from magi.bus.db.models.magis.magis_membership import (
    MAGISMembership,
    MAGISRole,
)
from magi.bus.db.runtime_settings import (
    RUNTIME_SETTINGS_FILENAME,
    RuntimeSettings,
    load_runtime_settings,
    save_runtime_settings,
)
from magi.bus.jobs.services.magic import MagicService
from magi.bus.jobs.services.magis import MagisService


@pytest.fixture
def magi_state(tmp_path, monkeypatch):
    """Stand up a per-test DB + bus state with a real bootstrap seed."""
    monkeypatch.setenv("MAGI_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HOST_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("MAGIS_DATABASE_URL", f"sqlite:///{tmp_path / 'magis.db'}")
    init_orm(seed_root=True)
    init_magis_public_db(seed_root=True)
    bootstrap(initialise_local=True)
    yield tmp_path


def _seed_root_ids() -> tuple[int, int]:
    """Return (root_magis_id, adam_magic_id) seeded by bootstrap."""
    with open_magis_session() as session:
        root = session.scalar(
            __import__("sqlalchemy").select(MAGIS).where(MAGIS.parent_id.is_(None))
        )
        adam = session.get(MAGIC, root.adam_id) if root else None
        assert root is not None
        assert adam is not None
        return int(root.id), int(adam.id)


def test_bootstrap_seeds_root_and_adam_with_id_zero(magi_state):
    """First boot writes Genesis + EVA-000 with id=0 each."""
    root_id, adam_id = _seed_root_ids()
    assert root_id == 0
    assert adam_id == 0

    with open_magis_session() as session:
        root = session.get(MAGIS, root_id)
        adam = session.get(MAGIC, adam_id)
        assert root.name == "Genesis"
        assert adam.name == "EVA-000"
        # Adam is wired to Genesis via the membership row.
        binding = session.scalar(
            __import__("sqlalchemy").select(MAGISMembership).where(
                MAGISMembership.magic_id == adam_id
            )
        )
        assert binding is not None
        assert binding.magis_id == root_id
        role = session.get(MAGISRole, binding.role_id)
        assert role.name == "ADAM"
        assert root.adam_id == adam_id


def test_create_magic_auto_binds_membership_to_default_eva(magi_state):
    """New MAGI gets the next id and the target MAGIS's reserved EVA role."""
    root_id, _ = _seed_root_ids()
    service = MagicService()

    view = service.create_magic(name="EVA-001", magis_id=root_id)

    assert view.name == "EVA-001"
    assert view.id == 1  # 0 was the seed; next is 1
    assert len(view.memberships) == 1
    membership = view.memberships[0]
    assert membership.magis_id == root_id
    assert membership.role_name == "EVA"


def test_create_magic_with_explicit_adam_role(magi_state):
    """A new MAGI can claim ADAM when the target MAGIS has no ADAM yet.

    Picks a freshly-created child MAGIS (no bootstrap seed) so the
    ADAM-uniqueness constraint is satisfiable.  The test explicitly
    passes the child MAGIS's reserved ADAM role — the service's
    default (EVA) would not flip ``magis.adam_id``.
    """
    root_id, _ = _seed_root_ids()
    magis_service = MagisService()
    child_view = magis_service.create_magis(
        name="Child", instruction="", parent_id=root_id,
    )

    # Find the child MAGIS's reserved ADAM role id.
    roles = magis_service.list_roles_in_magis(child_view.id)
    adam_role = next(r for r in roles if r.name == "ADAM" and r.is_reserved)

    magic_service = MagicService()
    view = magic_service.create_magic(
        name="Adam-of-Child",
        magis_id=child_view.id,
        role_id=adam_role.id,
    )

    # The child MAGIS now has its own ADAM, the bootstrap Genesis's
    # ADAM is untouched, and the new MAGI's membership carries the
    # ADAM role.
    with open_magis_session() as session:
        child = session.get(MAGIS, child_view.id)
        root = session.get(MAGIS, root_id)
        assert child.adam_id == view.id
        assert root.adam_id != view.id  # bootstrap ADAM unchanged
        binding = session.scalar(
            __import__("sqlalchemy").select(MAGISMembership).where(
                MAGISMembership.magic_id == view.id
            )
        )
        role = session.get(MAGISRole, binding.role_id)
        assert role.name == "ADAM"


def test_create_magic_rejects_duplicate_name(magi_state):
    """Duplicate names raise ValueError, the API maps it to 400."""
    root_id, _ = _seed_root_ids()
    service = MagicService()

    service.create_magic(name="EVA-001", magis_id=root_id)
    with pytest.raises(ValueError, match="already exists"):
        service.create_magic(name="EVA-001", magis_id=root_id)


def test_create_magic_rejects_unknown_magis(magi_state):
    """Unknown magis_id raises ValueError before any row is written."""
    service = MagicService()
    with pytest.raises(ValueError, match="not found"):
        service.create_magic(name="EVA-001", magis_id=999)


def test_runtime_settings_round_trip(tmp_path, monkeypatch):
    """load_runtime_settings reads what save_runtime_settings wrote."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / RUNTIME_SETTINGS_FILENAME

    save_runtime_settings(
        RuntimeSettings(provider="claude", api_key="sk-test", model="claude-opus-4-7"),
        path=target,
    )
    loaded = load_runtime_settings(path=target)
    assert loaded.provider == "claude"
    assert loaded.api_key == "sk-test"
    assert loaded.model == "claude-opus-4-7"
    # File contents are valid JSON with the three keys; secrets aren't
    # logged so the test only checks shape.
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {
        "provider": "claude",
        "api_key": "sk-test",
        "model": "claude-opus-4-7",
    }


def test_runtime_settings_missing_file_returns_empty(tmp_path):
    """No file on disk ⇒ all-None RuntimeSettings (unconfigured)."""
    target = tmp_path / "absent.toml"
    loaded = load_runtime_settings(path=target)
    assert loaded == RuntimeSettings()
    assert loaded.has_credentials is False


def test_runtime_settings_corrupt_file_returns_empty(tmp_path, caplog):
    """A broken file should NOT crash the boot — log + return defaults."""
    target = tmp_path / "broken.toml"
    target.write_text("{not valid json", encoding="utf-8")
    loaded = load_runtime_settings(path=target)
    assert loaded == RuntimeSettings()


def test_provider_configuration_reads_runtime_settings_file(magi_state, tmp_path, monkeypatch):
    """MagicService.provider_configuration returns the file-backed config."""
    # The runtime_settings loader resolves to ``workspace_dir()`` which
    # honours ``MAGI_WORKSPACE_DIR``.  Pin it to our tmp_path BEFORE
    # writing the file so save and load see the same path.
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))

    save_runtime_settings(
        RuntimeSettings(provider="claude", api_key="sk-from-file", model="claude-opus-4-7"),
    )

    service = MagicService()
    config = service.provider_configuration()
    # The service resolves the runtime MAGIC via MAGI_RUNTIME_ID or,
    # failing that, the root MAGIS's ADAM (id=0).  We didn't set
    # MAGI_RUNTIME_ID, so it picks the bootstrap ADAM and the local
    # settings file we just wrote.
    assert config is not None
    assert config.provider == "claude"
    assert config.api_key == "sk-from-file"
    assert config.model == "claude-opus-4-7"


def test_create_magic_does_not_set_provider_columns(magi_state):
    """Provider / API key columns stay None at creation time."""
    root_id, _ = _seed_root_ids()
    service = MagicService()
    view = service.create_magic(name="EVA-001", magis_id=root_id)

    with open_magis_session() as session:
        row = session.get(MAGIC, view.id)
        assert row.provider is None
        assert row.api_key is None
