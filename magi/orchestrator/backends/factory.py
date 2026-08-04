"""Backend factory — selects the active ``RuntimeBackend`` implementation.

The factory is the **only** place that reads ``MAGI_BACKEND``.  Every
other module resolves a backend via :func:`BackendFactory.create` so
swapping the deployment profile is a one-env-var change.  Phase 2
supports ``kubernetes`` (default) only; ``local_process`` raises
``NotImplementedError`` until Phase 4 lands the Local launcher.
"""

from __future__ import annotations

import os

from magi.orchestrator.backends.base import RuntimeBackend


def create() -> RuntimeBackend:
    """Resolve the backend selected by the ``MAGI_BACKEND`` env var.

    Defaults to ``"kubernetes"`` — the K8s Profile is the historical
    default and remains so until the Local Profile lands in Phase 6.
    The ``local_process`` selector raises because the implementation
    ships in Phase 4 (plan §4.2 + §12).
    """
    kind = os.environ.get("MAGI_BACKEND", "kubernetes").strip().lower()
    if kind in {"kubernetes", ""}:
        from magi.orchestrator.backends.kubernetes_compat import KubernetesEveBackendAdapter

        return KubernetesEveBackendAdapter()
    if kind == "local_process":
        raise NotImplementedError(
            "MAGI_BACKEND=local_process ships in Phase 4 of the Local Standalone "
            "Deployment plan; see docs/MAGI_LOCAL_STANDALONE_DEPLOYMENT_IMPLEMENTATION_PLAN.md."
        )
    raise ValueError(f"unsupported MAGI_BACKEND={kind!r}; expected 'kubernetes' or 'local_process'")


__all__ = ["create"]