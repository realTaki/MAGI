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
    from ..books.memoryBook import MemoryRow  # noqa: F401
    from ..books.messageBook import MessageRow  # noqa: F401
    from ..books.settingsBook import SettingRow  # noqa: F401
    from ..books.taskBook import TaskRow  # noqa: F401
    from ..books.toolsBook import ToolRow  # noqa: F401
    from ..jobs.callLLMJob import CallLLMJobRow  # noqa: F401
    from ..jobs.changeProviderNotify import ChangeProviderNotifyRow  # noqa: F401
    from ..jobs.chatNotify import ChatNotifyRow  # noqa: F401
    from ..jobs.contactJobs import (  # noqa: F401
        CreateContactJobRow,
        DeleteContactJobRow,
        GetContactJobRow,
        ListContactsJobRow,
        TouchContactJobRow,
        UpdateContactJobRow,
    )
    from ..jobs.contactNoteJobs import (  # noqa: F401
        CreateContactNoteJobRow,
        DeleteContactNoteJobRow,
        GetContactNoteJobRow,
        ListContactNotesJobRow,
        UpdateContactNoteJobRow,
    )
    from ..jobs.conversationJobs import (  # noqa: F401
        GetConversationForChannelJobRow,
        GetConversationJobRow,
        UpdateConversationSummaryJobRow,
    )
    from ..jobs.deliveryNotify import DeliveryNotifyRow  # noqa: F401
    from ..jobs.memoryJobs import (  # noqa: F401
        CreateMemoryJobRow,
        DeleteMemoryJobRow,
        GetMemoryJobRow,
        ListMemoriesJobRow,
        UpdateMemoryJobRow,
    )
    from ..jobs.messageJobs import (  # noqa: F401
        ArchiveMessagesJobRow,
        ListConversationMessagesJobRow,
        SearchContactMessagesJobRow,
        SearchConversationMessagesJobRow,
    )
    from ..jobs.promptJobs import (  # noqa: F401
        GetPromptJobRow,
        RegisterPromptJobRow,
        ResetPromptJobRow,
        SetPromptJobRow,
    )
    from ..jobs.runTaskNotify import RunTaskNotifyRow  # noqa: F401
    from ..jobs.runToolJob import RunToolJobRow  # noqa: F401
    from ..jobs.settingsJobs import (  # noqa: F401
        DeleteSettingJobRow,
        GetSettingJobRow,
        ListSettingsJobRow,
        SetSettingJobRow,
    )
    from ..jobs.skillJobs import GetSkillJobRow, ListSkillsJobRow  # noqa: F401
    from ..jobs.taskJobs import GetTaskJobRow, ListTasksJobRow  # noqa: F401
    from ..jobs.toolsJobs import (  # noqa: F401
        DeleteToolJobRow,
        GetToolJobRow,
        ListToolsJobRow,
        SetToolsJobRow,
    )

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
