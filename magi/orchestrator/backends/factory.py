"""Backend factory — selects the active ``RuntimeBackend`` implementation.

The factory is the **only** place that reads ``MAGI_BACKEND``.  Every
other module resolves a backend via :func:`create` so swapping the
deployment profile is a one-env-var change.

Phase 4 (``local_process``) is now actually implemented; the factory
instantiates :class:`LocalProcessRuntimeBackend` when the env var
selects Local.  K8s remains the default.
"""

from __future__ import annotations

import os

from magi.orchestrator.backends.base import RuntimeBackend


def create() -> RuntimeBackend:
    """Resolve the backend selected by the ``MAGI_BACKEND`` env var.

    Defaults to ``"kubernetes"`` (the historical production path).
    """
    kind = os.environ.get("MAGI_BACKEND", "kubernetes").strip().lower()
    if kind in {"kubernetes", ""}:
        from magi.orchestrator.backends.kubernetes_compat import KubernetesEvaBackendAdapter

        return KubernetesEvaBackendAdapter()
    if kind == "local_process":
        from magi.orchestrator.backends.local_process import LocalProcessRuntimeBackend

        return LocalProcessRuntimeBackend()
    raise ValueError(
        f"unsupported MAGI_BACKEND={kind!r}; expected 'kubernetes' or 'local_process'"
    )


__all__ = ["create"]
