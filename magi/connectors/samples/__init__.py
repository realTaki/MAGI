"""Sample connectors — concrete implementations of the protocol.

Each file in this package is a working example that
operators can copy + adapt. The connectors below are
intentionally small and platform-friendly so they serve
as a template rather than a production dependency:

  - :mod:`calendar` — macOS Calendar (osascript) + iCal file
    fallback. Useful for dev boxes; production deployments
    would substitute Google Calendar / iCloud CalDAV.

Adding a new connector:

  1. Drop a new ``your_connector.py`` next to ``calendar.py``.
  2. Subclass :class:`magi.connectors.base.Connector`.
  3. Define a ``factory(config: ConnectorConfig) -> Connector``
     factory function at module bottom.
  4. Call :func:`magi.connectors.registry.register_connector_factory`
     from the ``load_connectors`` boot path.
"""

from __future__ import annotations

__all__: list[str] = []