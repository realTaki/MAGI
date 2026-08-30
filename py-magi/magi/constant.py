"""Runtime defaults for the local MAGI service."""

from providers.worker import ProvidersWorker
from tools.worker import ToolsWorker

WORKSPACE_PATH = "workspace"
WORKERS = (ProvidersWorker, ToolsWorker)
