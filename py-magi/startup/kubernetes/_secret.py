"""Shared resolver for the per-MAGIS control secret on the K8s path.

The K8s client / service / resources modules all need the same HMAC key
to sign and verify inter-pod lifecycle requests, and to derive the
shared MAGIS Postgres password.  They run in different deployment
shapes (EVA pod, orchestrator pod, in-process library code) and cannot
share a ``Bus`` instance, so the secret is resolved independently by
each call site.

The DB-backed ``bus.control_secrets`` row is the single source of truth
at runtime.  ``open_bus(magis_url=...)`` opens the same MAGIS store
that the rest of the runtime reads, and the resolver caches the row
value once per process.

Resolution order:
  1. ``MAGI_CONTROL_SECRET`` env var — kept as a deployment-bridge
     override (a k8s Secret can be injected as env without this module
     knowing about k8s primitives).  This is the only path that does
     not require the MAGIS DB to exist, which is necessary during the
     cluster bootstrap that creates the DB.
  2. ``bus.control_secrets_book`` row — normal runtime path.
"""

from __future__ import annotations

import os
from functools import lru_cache

from old_bus import Bus, open_bus
from channels.api.proxy_auth import _control_secret_name, resolve_control_secret


@lru_cache(maxsize=1)
def _magis_bus() -> Bus | None:
    """Open the MAGIS bus once per process.

    Returns ``None`` if ``MAGIS_DATABASE_URL`` is unset or the bus
    cannot be opened; the caller falls back to the env-var path in
    that case.
    """
    url = os.environ.get("MAGIS_DATABASE_URL")
    if not url:
        return None
    try:
        return open_bus(magis_url=url)
    except Exception:
        return None


def get_control_secret() -> bytes | None:
    """Return the HMAC key for inter-pod signing / verification.

    Returns ``None`` if neither the env-var bridge nor the DB row has
    a value.  Callers raise with a deployment-side error in that case.
    """
    env_value = os.environ.get("MAGI_CONTROL_SECRET")
    if env_value:
        return env_value.encode()
    bus = _magis_bus()
    if bus is None:
        return None
    return resolve_control_secret(bus)


__all__ = ["get_control_secret"]
