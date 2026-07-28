"""Programmatic Alembic runner used during MAGI startup."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.engine import Engine

logger = logging.getLogger("magi.agent.db.alembic_runner")

_ALEMBIC_SCRIPT_LOCATION = Path(__file__).resolve().parent / "alembic"


def _find_alembic_ini() -> Path:
    """Find the config in a source checkout and in the runtime image.

    Deliberately does NOT look in ``Path.cwd()`` — a bare
    ``alembic upgrade head`` from a checkout root would find the
    right file but the ``alembic.ini`` it reads has an empty
    ``sqlalchemy.url`` (set on purpose; see alembic.ini), so the
    default SQLite target would be cwd-relative ``./magi.db``.
    Refusing the cwd lookup is the second line of defence; the
    primary one is the empty URL above.
    """
    package_root = Path(__file__).resolve().parents[2]  # ``.../magi``
    candidates = (
        package_root.parent / "alembic.ini",  # source checkout / /app
        Path("/app/alembic.ini"),  # production image runtime stage
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return candidates[0]


def _config_for_state_dir(state_dir: str | Path):
    """Build an Alembic config pointed at one workspace database."""
    from alembic.config import Config

    state_path = Path(state_dir).resolve()
    state_path.mkdir(parents=True, exist_ok=True)
    db_path = state_path / "magi.db"

    alembic_ini = _find_alembic_ini()
    if not alembic_ini.is_file():
        raise RuntimeError(
            f"Alembic configuration is missing: {alembic_ini}. "
            "The deployment image must include alembic.ini."
        )

    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(_ALEMBIC_SCRIPT_LOCATION))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def stamp_baseline(state_dir: str | Path) -> None:
    """Stamp a legacy database after the compatibility pass has run."""
    from alembic import command

    config = _config_for_state_dir(state_dir)
    command.stamp(config, "0001_baseline")


def upgrade_head(state_dir: str | Path, engine: Engine | None = None) -> None:
    """Apply all committed migrations to ``state_dir``.

    ``engine`` is accepted for the caller's clarity and future integration,
    but Alembic creates a short-lived migration engine from the same SQLite
    URL. The application engine is not reused while its ORM sessions are
    still being initialised. We also ``dispose()`` the application engine
    afterwards so any Alembic-borrowed connection in the pool doesn't
    collide with the first ORM session the caller opens on the same DB
    file (``BEGIN IMMEDIATE`` will deadlock otherwise — SQLite holds the
    writer reservation on the queued backend).
    """
    from alembic import command

    state_path = Path(state_dir).resolve()
    config = _config_for_state_dir(state_path)

    logger.info("running Alembic migrations", extra={"state_dir": str(state_path)})
    command.upgrade(config, "head")
    # Force the application engine to drop any pooled connection
    # that may still be holding a SQLite transaction handle. The
    # next call to ``get_engine()`` recreates a fresh pool.
    if engine is not None:
        engine.dispose()
