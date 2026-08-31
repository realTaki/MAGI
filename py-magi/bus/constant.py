"""Default configuration for workers attached to one BUS."""

from __future__ import annotations

import json
from dataclasses import dataclass

from channels.asp import AspWorker
from providers.client import options
from providers.worker import ProvidersWorker
from tools.worker import ToolsWorker

from .BaseWorker import BaseWorker


@dataclass(frozen=True)
class WorkerConfig:
    worker: type[BaseWorker]
    settings: dict[str, str]


WORKERS = (
    WorkerConfig(ProvidersWorker, {"options": json.dumps(options(), ensure_ascii=False)}),
    WorkerConfig(ToolsWorker, {}),
    WorkerConfig(AspWorker, {"handle": "", "base": "", "token": ""}),
)
