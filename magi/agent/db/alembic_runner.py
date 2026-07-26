"""Programmatic Alembic runner used during MAGI startup."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.engine import Engine

logger = logging.getLogger("magi.agent.db.alembic_runner")

_ALEMBIC_SCRIPT_LOCATION = Path(__file__).resolve().parent / "alembic"


def _find_alembic_ini() -> Path:
    """Find the config in a source checkout and in the runtime image."""
    package_root = Path(__file__).resolve().parents[2]  # ``.../magi``
    candidates = (
        package_root.parent / "alembic.ini",  # source checkout / /app
        Path("/app/alembic.ini"),  # production image runtime stage
        Path.cwd() / "alembic.ini",  # explicit CLI invocation from a checkout
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return candidates[0]


def upgrade_head(state_dir: str | Path, engine: Engine | None = None) -> None:
    """Apply all committed migrations to ``state_dir``.

    ``engine`` is accepted for the caller's clarity and future integration,
    but Alembic creates a short-lived migration engine from the same SQLite
    URL. The application engine is not reused while its ORM sessions are
    still being initialised.
    """
    from alembic import command
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

    logger.info("running Alembic migrations", extra={"state_dir": str(state_path)})
    command.upgrade(config, "head")
