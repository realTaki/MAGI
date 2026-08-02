
"""Programmatic Alembic runner used during MAGI startup."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.engine import Engine

logger = logging.getLogger("magi.db.alembic_runner")

_ALEMBIC_SCRIPT_LOCATION = Path(__file__).resolve().parent / "alembic"

#: The single revision every current MAGI database should be at. The rebase
#: guard below is only for historical development snapshots whose revision
#: files have since been folded or renamed; normal upgrades run every
#: committed revision through this head.
CANONICAL_HEAD = "0005_agent_bus"


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
    package_root = Path(__file__).resolve().parents[1]  # ``.../magi``
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


def _known_revisions(config) -> set[str]:
    """Return the set of revisions Alembic can currently see."""
    from alembic.script import ScriptDirectory

    return {
        rev.revision
        for rev in ScriptDirectory.from_config(config).walk_revisions()
    }


def _rebase_to_canonical_head(
    state_dir: str | Path, config, *, force: bool = False,
) -> bool:
    """Re-stamp any stale ``alembic_version`` to ``CANONICAL_HEAD``.

    Returns ``True`` when a stamp happened. ``False`` when the
    row is already at the canonical head (or the row is
    absent, which is the "fresh DB" case handled by Alembic
    itself: it creates ``alembic_version`` and stamps it
    after running the baseline).

    The rebasing logic kicks in when the existing row names a
    revision Alembic can no longer find — typically because the
    follow-on migration was deleted in a refactor. The shape of
    the database is already correct (the deleted migration's
    effect is folded into ``0001_baseline``); we just need the
    bookkeeping row to match.

    Implementation note: Alembic's ``stamp`` cannot target a
    revision that the script directory doesn't ship, so when
    the current row points at a now-deleted revision we have
    to blank the row first (``DELETE FROM alembic_version``)
    and then re-stamp. The DELETE is safe: a fresh stamp
    re-inserts the row immediately, and a crash in between
    leaves the DB in the "no alembic_version" state which
    ``init_orm``'s legacy branch handles correctly on the
    next boot.

    ``force=True`` re-stamps even when the row already names a
    known revision. Not used in the normal startup path — kept
    as an escape hatch for ops debugging.
    """
    from alembic import command
    from sqlalchemy import create_engine, text

    db_path = Path(state_dir).resolve() / "magi.db"
    if not db_path.is_file():
        return False

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            # Probe whether ``alembic_version`` exists at all.
            # On a truly fresh DB the table is created later by
            # the baseline itself; reading it now would crash.
            present = conn.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='alembic_version'"
                )
            ).first() is not None
            if not present:
                return False

            row = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).first()
            current = row[0] if row is not None else None

            if current == CANONICAL_HEAD and not force:
                return False

            known = _known_revisions(config)
            needs_rebase = force or current is None or current not in known
            if not needs_rebase:
                return False

            if current is not None:
                logger.warning(
                    "alembic_version row points at unknown revision %r; "
                    "re-stamping to canonical head %r",
                    current, CANONICAL_HEAD,
                )
                # Blank the bookkeeping row before re-stamping.
                # ``command.stamp`` refuses to retarget from a
                # revision it can't resolve.
                conn.execute(text("DELETE FROM alembic_version"))
                conn.commit()
    finally:
        engine.dispose()

    command.stamp(config, CANONICAL_HEAD)
    return True


def stamp_baseline(state_dir: str | Path) -> None:
    """Stamp a legacy database after the compatibility pass has run."""
    from alembic import command

    config = _config_for_state_dir(state_dir)
    command.stamp(config, CANONICAL_HEAD)


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

    # Adopt any DB whose alembic_version points at a revision
    # Alembic no longer ships. The codebase is in dev mode
    # and refactors occasionally fold follow-on migrations
    # back into ``0001_baseline``; without this guard, a
    # developer returning from a git pull would see Alembic
    # raise ``Can't locate revision`` and the boot would
    # fail. The database shape is already correct (the
    # folded migration's effect is in the new baseline);
    # we're just retargeting the bookkeeping row.
    _rebase_to_canonical_head(state_path, config)

    logger.info("running Alembic migrations", extra={"state_dir": str(state_path)})
    command.upgrade(config, "head")
    # Force the application engine to drop any pooled connection
    # that may still be holding a SQLite transaction handle. The
    # next call to ``get_engine()`` recreates a fresh pool.
    if engine is not None:
        engine.dispose()
