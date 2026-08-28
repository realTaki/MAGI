"""Temporary launcher settings. Replace with real config later."""

from magi.providers.worker import ProvidersWorker

DATABASE_URL = "sqlite://"
WORKSPACE_PATH = "workspace"
WORKERS = (ProvidersWorker,)
