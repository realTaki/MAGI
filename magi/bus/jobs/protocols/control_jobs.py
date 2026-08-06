"""Wire format for the BUS's transient control-job queue.

A :class:`ControlJob` row is a short-lived signal between a BUS
producer (``save_runtime_settings``, future endpoints) and a BUS
consumer (``magi.providers.worker.ProvidersWorker``). It is **not**
an audit record — the consumer deletes the row as soon as it has
acted on it, and the queue never accumulates history. Hook
evaluations and ``llm_attempts`` own durable traces of what
actually happened; this queue only carries "wake up and refresh".

Why a queue instead of an in-process callback
=============================================

The runtime container runs as a single replica (SQLite single-writer;
``deploy/k8s/base/deployment.yaml`` pins ``replicas: 1``) and the
provider worker shares the process with the WebUI / API endpoints,
so a callback would work in v0. We pick the durable row anyway:

  * the same primitive works once multi-replica is allowed
    (kustomize overlay, ``k8s-dev`` profile);
  * the existing 0.25 s provider-worker poll gives a reasonable
    upper bound on rebuild latency without adding a wake primitive;
  * one row per save is cheaper than wiring a callback registry
    that future endpoints would have to remember to call.

Design principle
================

The worker does not branch on ``kind``. Today the only kind is
``"provider.config_changed"``; the literal is closed (adding a
fourth kind requires updating this file). The payload is
debug-only — it must never carry the API key.
"""

from __future__ import annotations

from typing import Literal


# Closed set of :attr:`ControlJob.kind` labels. The provider worker
# only acts on ``"provider.config_changed"``; anything else is left
# in the queue for a future consumer.
ControlJobKind = Literal[
    "provider.config_changed",
]


# Single source of truth for the kind string. Both the BUS producer
# (``save_runtime_settings``) and the consumer
# (``ProvidersWorker._run``) import this constant so a typo fails
# import instead of silently misrouting.
PROVIDER_CONFIG_CHANGED: ControlJobKind = "provider.config_changed"


__all__ = [
    "ControlJobKind",
    "PROVIDER_CONFIG_CHANGED",
]