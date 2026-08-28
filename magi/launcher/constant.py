"""Temporary launcher settings. Replace with real config later."""

from magi.providers.worker import ProvidersWorker

DATABASE_URL = "sqlite://"
WORKERS = (ProvidersWorker,)
