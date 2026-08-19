"""Apply Firmware SQL schema. Alembic owns changes after the first cut."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import MetaData

FIRMWARE_DIR = Path(__file__).resolve().parent


def firmware_metadata() -> MetaData:
    """Load every Firmware Book Row onto BaseRecordMixin.metadata."""
    from ..base.BaseRecordMixin import BaseRecordMixin
    from .books.conversationBook import ConversationRow  # noqa: F401
    from .books.messageBook import MessageRow  # noqa: F401

    return BaseRecordMixin.metadata


def prepare_schema(backend) -> None:
    """Create or upgrade SQL tables for Firmware Books / Jobs."""
    engine = getattr(backend, "engine", None)
    if engine is None:
        return
    try:
        _upgrade(engine)
    except Exception:
        firmware_metadata().create_all(engine)


def _upgrade(engine) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(FIRMWARE_DIR))
    cfg.set_main_option("version_locations", str(FIRMWARE_DIR / "versions"))
    cfg.set_main_option("path_separator", "os")
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "head")
