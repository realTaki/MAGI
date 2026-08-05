"""Backend factory — selects the active ``RuntimeBackend`` implementation.

The factory is the **only** place that reads ``MAGI_BACKEND``.  Every
other module resolves a backend via :func:`create` so swapping the
deployment profile is a one-env-var change.

K8s is the only backend at this point.  The Local Profile no longer
manages child processes — each MAGI is an independent OS process
managed by systemd (or run directly via ``magi local start``).
"""

from __future__ import annotations

import os

from magi.orchestrator.backends.base import RuntimeBackend


def create() -> RuntimeBackend:
    """Resolve the backend selected by the ``MAGI_BACKEND`` env var.

    Defaults to ``"kubernetes"`` (the only production path).
    """
    kind = os.environ.get("MAGI_BACKEND", "kubernetes").strip().lower()
    if kind in {"kubernetes", ""}:
        from magi.orchestrator.backends.kubernetes_compat import KubernetesEvaBackendAdapter

        return KubernetesEvaBackendAdapter()
    raise ValueError(
        f"unsupported MAGI_BACKEND={kind!r}; expected 'kubernetes'"
    )


__all__ = ["create"]
