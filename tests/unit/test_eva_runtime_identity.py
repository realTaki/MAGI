"""Runtime identity is read from a provisioned RuntimeSpec, never env aliases."""

from __future__ import annotations

import pytest

from magi.startup.config import ConfigurationError
from magi.startup.spec import load_runtime_spec


def test_runtime_requires_a_persisted_spec(tmp_path):
    with pytest.raises(ConfigurationError, match="not provisioned"):
        load_runtime_spec(tmp_path / "eva-001")


def test_runtime_rejects_invalid_persisted_spec(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "runtime.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="invalid runtime specification"):
        load_runtime_spec(tmp_path)
