"""Deployment path layout (Composition-Root config object).

The ``LocalPathLayout`` dataclass is the single source of truth for the
filesystem layout used by one MAGI runtime.  The K8s launcher constructs
one that mirrors the legacy ``/workspace`` tree; the Local launcher
constructs one rooted under the OS-specific data directory
(``~/Library/Application Support/MAGI`` on macOS,
``%LOCALAPPDATA%\\MAGI`` on Windows, ``$XDG_DATA_HOME/magi`` on Linux).

Business modules **never** import this dataclass.  They each receive a
minimal projection of the layout they actually need (e.g. an agent
worker receives ``state_dir`` only; a plugin receives ``audit_log_path``
only).  See plan §5.3.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LocalPathLayout:
    """Filesystem layout for one MAGI runtime.

    Single required argument: ``data_root``.  All other paths are derived
    inside :meth:`__post_init__`.  No env-var reads happen here — the
    Composition Root is the only place that decides which layout to build.

    Layout under ``data_root``::

        <data_root>/
        ├── control/
        │   ├── local-registry.db
        │   ├── control-secret
        │   ├── launcher.json
        │   └── logs/
        ├── MAGIC/<runtime-id>-<slug>/workspace/
        │   ├── memories/magi.db
        │   ├── skills/
        │   ├── SOUL.md
        │   ├── logs/
        │   └── tmp/
        └── MAGIS/<magis-id>-<slug>/magis.db
    """

    data_root: Path

    # Derived (post_init)
    state_dir: Path = None  # type: ignore[assignment]
    workspace: Path = None  # type: ignore[assignment]
    local_db: Path = None  # type: ignore[assignment]
    skills_dir: Path = None  # type: ignore[assignment]
    soul_path: Path = None  # type: ignore[assignment]
    logs_dir: Path = None  # type: ignore[assignment]
    temp_dir: Path = None  # type: ignore[assignment]
    magis_workspace: Path = None  # type: ignore[assignment]
    audit_log_path: Path = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Use object.__setattr__ because the dataclass is frozen.
        data_root = Path(self.data_root).expanduser().resolve()
        object.__setattr__(self, "data_root", data_root)
        object.__setattr__(self, "state_dir", data_root / "state")
        object.__setattr__(self, "workspace", data_root / "workspace")
        object.__setattr__(self, "local_db", self.state_dir / "magi.db")
        object.__setattr__(self, "skills_dir", self.workspace / "skills")
        object.__setattr__(self, "soul_path", self.workspace / "SOUL.md")
        object.__setattr__(self, "logs_dir", self.workspace / "logs")
        object.__setattr__(self, "temp_dir", self.workspace / "tmp")
        object.__setattr__(self, "magis_workspace", data_root / "MAGIS")
        object.__setattr__(self, "audit_log_path", data_root / "logs" / "audit.log")

    @classmethod
    def from_platform(cls) -> "LocalPathLayout":
        """Build the OS-specific Local Profile layout.

        Honours ``$MAGI_DATA_ROOT`` (used by tests / advanced operators) when
        set; otherwise picks the platformdirs-equivalent data directory for
        ``MAGI``.  No external dependency: re-implements the XDG / Windows
        conventions inline to avoid a new third-party requirement.
        """
        override = os.environ.get("MAGI_DATA_ROOT")
        if override:
            return cls(Path(override))
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            return cls(Path(base) / "MAGI")
        # macOS / Linux: XDG-style, falling back to ~/.local/share.
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return cls(Path(xdg) / "magi")
        return cls(Path.home() / ".local" / "share" / "magi")

    @classmethod
    def from_container_workspace(cls, prefix: str = "/workspace") -> "LocalPathLayout":
        """Reproduce the K8s container layout under ``prefix``.

        Used by the K8s launcher / existing container entry points — the
        Composition Root passes the legacy container prefix and the layout
        mirrors what the runtime used before the refactor: state under
        ``<prefix>/memories``, workspace at ``<prefix>``, SQLite at
        ``<prefix>/memories/magi.db``.  This guarantees Phase 1 is
        bit-identical for the K8s Profile.
        """
        prefix_path = Path(prefix).resolve()
        layout = cls(prefix_path / "magi-data")
        # Override the derived paths to mirror the legacy K8s layout.
        object.__setattr__(layout, "state_dir", prefix_path / "memories")
        object.__setattr__(layout, "workspace", prefix_path)
        object.__setattr__(layout, "local_db", prefix_path / "memories" / "magi.db")
        object.__setattr__(layout, "skills_dir", prefix_path / "skills")
        object.__setattr__(layout, "soul_path", prefix_path / "SOUL.md")
        object.__setattr__(layout, "logs_dir", prefix_path / "logs")
        object.__setattr__(layout, "temp_dir", prefix_path / "tmp")
        object.__setattr__(layout, "magis_workspace", prefix_path.parent / "magis")
        object.__setattr__(layout, "audit_log_path", prefix_path / "audit" / "audit.log")
        return layout