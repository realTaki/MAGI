"""Apply Firmware SQL schema. Alembic owns changes after the first cut."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import MetaData

VERSIONS_DIR = Path(__file__).resolve().parent
_REVISION = re.compile(r"\d+\.\d+\.\d+\.py$")


def firmware_metadata() -> MetaData:
    """Load every Firmware Book / Job Row onto BaseRecordMixin.metadata."""
    from ...base.BaseBook import BaseRecordMixin
    from ..books.contactBook import ContactRow  # noqa: F401
    from ..books.contactNoteBook import ContactNoteRow  # noqa: F401
    from ..books.conversationBook import ConversationRow  # noqa: F401
    from ..books.messageBook import MessageRow  # noqa: F401
    from ..books.settingsBook import SettingRow  # noqa: F401
    from ..books.tokenUsageBook import TokenUsageRow  # noqa: F401
    from ..jobs.callLLMJob import CallLLMJobRow  # noqa: F401
    from ..jobs.changeProviderJob import ChangeProviderJobRow  # noqa: F401
    from ..jobs.conversationJobs import (  # noqa: F401
        CreateConversationJobRow,
        UpdateConversationSummaryJobRow,
    )
    from ..jobs.messageJobs import (  # noqa: F401
        AppendMessageJobRow,
        ArchiveMessagesJobRow,
        ListConversationMessagesJobRow,
    )
    from ..jobs.settingsJobs import (  # noqa: F401
        DeleteSettingJobRow,
        GetSettingJobRow,
        ListSettingsJobRow,
        SetSettingJobRow,
    )
    from ..jobs.tokenUsageJobs import RecordTokenUsageJobRow  # noqa: F401

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
    from alembic.script.base import Script

    cfg = Config()
    cfg.set_main_option("script_location", str(VERSIONS_DIR))
    cfg.set_main_option("version_locations", str(VERSIONS_DIR))
    cfg.set_main_option("path_separator", "os")
    cfg.attributes["connection"] = engine

    list_py = Script._list_py_dir.__func__

    def only_revisions(cls, scriptdir, path):
        return [p for p in list_py(cls, scriptdir, path) if _REVISION.fullmatch(p.name)]

    Script._list_py_dir = classmethod(only_revisions)
    try:
        command.upgrade(cfg, "head")
    finally:
        Script._list_py_dir = classmethod(list_py)
