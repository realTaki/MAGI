"""LLM calls: :mod:`providers.worker`. Hosts: :mod:`providers.client`."""

from __future__ import annotations

from typing import Any

__all__ = ["ProvidersWorker"]


def __getattr__(name: str) -> Any:
    if name == "ProvidersWorker":
        from providers.worker import ProvidersWorker

        return ProvidersWorker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
