"""Runtime defaults for the local MAGI service."""

from pathlib import Path

from providers.worker import ProvidersWorker
from tools.worker import ToolsWorker

LOCAL_HOST = "127.0.0.1"
FIRST_PORT = 42070
WORKERS = (ProvidersWorker, ToolsWorker)


def workspace_path(name: str) -> Path:
    """Return one MAGI's fixed local workspace."""
    return Path.home() / ".magi" / name / "workspace"
