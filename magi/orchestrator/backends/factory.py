"""Backend factory — selects the active ``RuntimeBackend`` implementation.

The factory is the **only** place that reads ``MAGI_BACKEND``.  Every
other module resolves a backend via :func:`create` so swapping the
deployment profile is a one-env-var change.

Two backends ship today:

- :class:`KubernetesEvaBackendAdapter` — K8s Profile (production path).
- :class:`CLIProcessRuntimeBackend` — CLI Profile; spawns one
  MAGI subprocess per ``bus.runtime.start`` call.

``magi cli start <name>`` injects ``MAGI_BACKEND=cli`` automatically
before invoking :func:`BackendDispatcherService.start`.  The K8s default
applies everywhere else.
"""

from __future__ import annotations

import os

from magi.orchestrator.backends.base import RuntimeBackend


def create() -> RuntimeBackend:
    """Resolve the backend selected by the ``MAGI_BACKEND`` env var.

    Defaults to ``"kubernetes"``.  The ``"cli"`` branch activates
    :class:`CLIProcessRuntimeBackend` for the CLI Profile;
    ``magi cli`` injects ``MAGI_BACKEND=cli`` automatically.
    """
    kind = os.environ.get("MAGI_BACKEND", "kubernetes").strip().lower()
    if kind in {"kubernetes", ""}:
        from magi.orchestrator.backends.kubernetes_compat import (
            KubernetesEvaBackendAdapter,
        )

        return KubernetesEvaBackendAdapter()
    if kind == "cli":
        from magi.orchestrator.backends.cli_process import (
            CLIProcessRuntimeBackend,
        )

        return CLIProcessRuntimeBackend()
    raise ValueError(
        f"unsupported MAGI_BACKEND={kind!r}; expected 'kubernetes' or 'cli'"
    )


__all__ = ["create"]
