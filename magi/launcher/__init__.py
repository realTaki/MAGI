"""Composition-root primitives for one MAGI-BUS runtime."""

from .launcher import Launcher, WorkerSpec
from .worker import BaseWorker, load_required_slots

__all__ = ["BaseWorker", "Launcher", "WorkerSpec", "load_required_slots"]
