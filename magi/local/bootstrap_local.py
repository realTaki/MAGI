"""Phase 1/3 Local Profile Composition Root.

Builds the :class:`magi.bus.Bus` facade for a Local Profile deployment.

Phase 1:  only the path layout is constructed.  The Local launcher calls
          this with a ``data_root`` (``MAGI_DATA_ROOT`` or platform default)
          and the bootstrap defers to ``magi.bus.bootstrap`` so the rest of
          the wiring stays shared with the K8s Profile.

Phase 3:  gains the ``LocalMagisEngine`` injection so the local MAGIS has
          its own SQLite instead of the Adam's private database.
"""

from __future__ import annotations

from pathlib import Path

from magi.bus import Bus, bootstrap
from magi.deploy import LocalPathLayout


def bootstrap_local(
    data_root: Path | str,
    *,
    initialise: bool = False,
    magis_dir: Path | str | None = None,
) -> Bus:
    """Build the Local Profile BUS facade rooted at ``data_root``.

    ``data_root`` becomes the root of the
    :class:`~magi.deploy.path_layout.LocalPathLayout`.  All downstream
    workers receive their ``state_dir`` from this layout via the BUS
    facade — no business module reaches back to the layout itself.

    When ``initialise=True`` the function bootstraps the on-disk SQLite
    schema (idempotent — safe to call on every launch).  Phase 6's
    launcher is the canonical caller; tests may pass ``initialise=True``
    to set up a fresh ``tmp_path`` fixture.

    ``magis_dir`` (Phase 3) overrides the per-MAGIS SQLite location; when
    ``None`` the function picks ``<data_root>/MAGIS/local/magis.db``.  The
    resulting engine is injected into the BUS so the public schema lives
    outside the Adam's private database.
    """
    layout = LocalPathLayout(Path(data_root))
    if magis_dir is None:
        magis_dir = Path(data_root).expanduser().resolve() / "MAGIS" / "local"
    magis_dir = Path(magis_dir)
    magis_dir.mkdir(parents=True, exist_ok=True)

    # Phase 3 — build the per-MAGIS SQLite engine eagerly so the BUS
    # facade receives it via bootstrap(..., magis_engine=...).
    from magi.bus.db.magis.local_engine import build as build_local_engine

    magis_engine = build_local_engine(magis_dir)
    return bootstrap(
        str(layout.state_dir),
        initialise_local=initialise,
        magis_engine=magis_engine,
    )