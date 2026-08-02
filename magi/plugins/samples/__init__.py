"""Sample plugins — concrete implementations of the protocol.

Each file in this package is a working example that
operators can copy + adapt. The plugins below are
intentionally small and dependency-free so they serve
as a template rather than a production dependency:

  - :mod:`audit_log` — writes one structured record per
    tool call / channel send / LLM call to a configurable
    append-only store. The canonical "show me what
    the system did" plugin.

Adding a new plugin:

  1. Drop a new ``your_plugin.py`` next to ``audit_log.py``.
  2. Implement the :class:`magi.plugins.base.Plugin`
     protocol (an object with ``name``, ``version``,
     ``register(bus)``, and ``shutdown()``).
  3. Call ``register_plugin(...)`` at module bottom to
     install it eagerly on import (mirrors the
     ``install_audit_log_plugin()`` pattern below).
"""

from __future__ import annotations

__all__: list[str] = []