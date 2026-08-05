"""Backend factory — selects the active ``RuntimeBackend`` implementation.

The factory is the **only** place that reads ``MAGI_BACKEND``.  Every
other module resolves a backend via :func:`create` so swapping the
deployment profile is a one-env-var change.

Two backends ship today:

- :class:`KubernetesEvaBackendAdapter` — K8s Profile (production path).
- :class:`LocalProcessRuntimeBackend` — Local Profile; spawns one
  MAGI subprocess per ``bus.runtime.start`` call.

``magi local start <name>`` injects ``MAGI_BACKEND=local`` automatically
before invoking :func:`BackendDispatcherService.start`.  The K8s default
applies everywhere else.
"""

from __future__ import annotations

import os

from magi.orchestrator.backends.base import RuntimeBackend


def create() -> RuntimeBackend:
    """Resolve the backend selected by the ``MAGI_BACKEND`` env var.

    Defaults to ``"kubernetes"``.  The ``"local"`` branch activates
    :class:`LocalProcessRuntimeBackend` for the Local Profile;
    ``magi local`` injects ``MAGI_BACKEND=local`` automatically.
    """
    kind = os.environ.get("MAGI_BACKEND", "kubernetes").strip().lower()
    if kind in {"kubernetes", ""}:
        from magi.orchestrator.backends.kubernetes_compat import (
            KubernetesEvaBackendAdapter,
        )

        return KubernetesEvaBackendAdapter()
    if kind == "local":
        from magi.orchestrator.backends.local_process import (
            LocalProcessRuntimeBackend,
        )

        return LocalProcessRuntimeBackend()
    raise ValueError(
        f"unsupported MAGI_BACKEND={kind!r}; expected 'kubernetes' or 'local'"
    )


__all__ = ["create"]
