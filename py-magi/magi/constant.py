"""Runtime defaults for one local MAGI."""

from pathlib import Path

from providers.worker import ProvidersWorker
from tools.worker import ToolsWorker

WORKERS = (ProvidersWorker, ToolsWorker)


def workspace_path(handle: str) -> Path:
    """Return this MAGI's workspace. Handle is identity; the directory is stable."""
    key = handle[1:] if handle.startswith("@") else handle
    return Path.home() / ".magi" / key / "workspace"
