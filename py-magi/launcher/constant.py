"""Temporary launcher settings. Replace with real config later."""

from providers.worker import ProvidersWorker
from tools.worker import ToolsWorker

WORKSPACE_PATH = "workspace"
WORKERS = (ProvidersWorker, ToolsWorker)
