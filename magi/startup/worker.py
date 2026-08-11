"""Backwards-compat shim for :class:`magi.runtime_worker.RuntimeWorker`.

:class:`RuntimeWorker` was moved out of the composition-root subtree
(:mod:`magi.startup.worker`) to the package root
(:mod:`magi.runtime_worker`) so the bus layer no longer depends on
:mod:`magi.startup`.  See ARCHITECTURE_REVIEW_2026-08-10 P2 for the
rationale.

This shim emits a :class:`DeprecationWarning` so any code still
importing from ``magi.startup.worker`` shows up in test runs
(``pytest`` enables ``DeprecationWarning`` by default) and can be
fixed. New code must import from :mod:`magi.runtime_worker` directly.
"""

from __future__ import annotations

import warnings

from magi.runtime_worker import RuntimeWorker

warnings.warn(
    "importing RuntimeWorker from magi.startup.worker is deprecated; "
    "import from magi.runtime_worker instead (ARCHITECTURE_REVIEW "
    "2026-08-10 P2)",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["RuntimeWorker"]
