"""Post-bootstrap startup context (plan §10).

BUS / Agent / Channels / Tools consume this struct rather than re-reading
environment variables. Once the context exists the rest of the runtime
never touches ``os.environ`` for startup knobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StartupContext:
    """Resolved post-bootstrap identity handed to the runtime layer.

    Fields map 1:1 to plan §10:

    - ``host_workspace_dir`` — operator's host root
    - ``workspace_dir``      — per-MAGI workspace (derived)
    - ``magi_name``          — display name
    - ``magi_id``            — MAGIS identity (MAGIC.id)
    - ``magis_database_url`` — DSN of the MAGIS public database
    - ``private_database_url`` — DSN of this MAGI's private SQLite
    - ``is_first_magi``      — True for the ``eva-000`` Genesis bootstrap
    """

    host_workspace_dir: Path
    workspace_dir: Path
    magi_name: str
    magi_id: str
    magis_database_url: str
    private_database_url: str
    is_first_magi: bool

    @property
    def magi_slug(self) -> str:
        """Same as :attr:`magi_name` — names are slugs by plan §4.2."""
        return self.magi_name


__all__ = ["StartupContext"]