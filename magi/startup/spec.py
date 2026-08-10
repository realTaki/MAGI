"""Persisted, immutable-at-runtime specifications for MAGI processes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from magi.startup.config import ConfigurationError
from magi.startup.paths import resolve_runtime_state_path


@dataclass(frozen=True, slots=True)
class RuntimeSpec:
    """Everything a node runtime may need after provisioning is complete."""

    magi_name: str
    magi_id: str
    magis_name: str
    magis_database_url: str
    runtime_port: int
    is_first_magi: bool


def write_runtime_spec(workspace_dir: Path, spec: RuntimeSpec) -> None:
    path = resolve_runtime_state_path(workspace_dir)
    path.write_text(json.dumps(asdict(spec), indent=2, sort_keys=True), encoding="utf-8")


def load_runtime_spec(workspace_dir: Path) -> RuntimeSpec:
    retired = workspace_dir / "magi.db"
    if retired.exists():
        raise ConfigurationError(
            f"retired node database exists at {retired}; clean the workspace before running MAGI"
        )
    path = resolve_runtime_state_path(workspace_dir)
    if not path.is_file():
        raise ConfigurationError(
            f"node {workspace_dir.name!r} is not provisioned; run `magi init` or `magi node create` first"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RuntimeSpec(
            magi_name=str(data["magi_name"]),
            magi_id=str(data["magi_id"]),
            magis_name=str(data["magis_name"]),
            magis_database_url=str(data["magis_database_url"]),
            runtime_port=int(data["runtime_port"]),
            is_first_magi=bool(data["is_first_magi"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"invalid runtime specification at {path}") from exc


__all__ = ["RuntimeSpec", "load_runtime_spec", "write_runtime_spec"]
