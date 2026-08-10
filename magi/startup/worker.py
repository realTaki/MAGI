"""Backwards-compat re-export of :class:`magi.runtime_worker.RuntimeWorker`.

:class:`RuntimeWorker` was moved out of the composition-root subtree
(:mod:`magi.startup.worker`) to the package root
(:mod:`magi.runtime_worker`) so the bus layer no longer depends on
:mod:`magi.startup`.  See ARCHITECTURE_REVIEW_2026-08-10 P2 for the
rationale.

This shim keeps the old import path working for any caller that has
not migrated yet; it is intentionally not a deprecation warning so
unrelated tests can keep passing during the rollout.  New code should
import from :mod:`magi.runtime_worker` directly.
"""

from __future__ import annotations

from magi.runtime_worker import RuntimeWorker

__all__ = ["RuntimeWorker"]