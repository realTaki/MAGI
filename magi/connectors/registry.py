"""Connector registry — process-wide connector instances.

Mirrors :mod:`magi.channels.dispatcher` (channels) and
:mod:`magi.tools.registry` (tools) so the runtime has a
single pattern for "register at boot, look up by name,
clean up at shutdown".

A connector *instance* lives here after
:func:`load_connectors` reads the private ``connector_configs``
table. The lookup API is ``get_connector(name, instance_id)``
(returns the constructed, connected instance) or
:func:`list_connectors` (returns all loaded (name, instance_id)
pairs).
"""

from __future__ import annotations

import logging
from typing import Any

from magi.connectors.base import Connector, ConnectorConfig

logger = logging.getLogger("magi.connectors.registry")


# Active instances after load_connectors ran.
# Keyed by ``(name, instance_id)`` — same shape as the
# config key so ``registry.get(c.key())`` finds the same
# instance the loader constructed.
_INSTANCES: dict[tuple[str, str], Connector] = {}

# Optional loader hooks. ``register_connector_factory(name, fn)``
# installs a *factory* that ``load_connectors`` calls to
# construct one :class:`Connector` for every enabled config.
# Mirrors ``magi.channels.dispatcher.register_adapter``.
_FACTORIES: dict[str, Any] = {}


def register_connector_factory(
    name: str,
    factory: Any,
) -> None:
    """Install ``factory`` under connector type ``name``.

    ``factory(config: ConnectorConfig) -> Connector`` is
    called once per enabled config at boot. Re-registering
    the same name replaces the prior factory — same
    idempotency contract as :func:`magi.channels.dispatcher.register_adapter`.
    """
    _FACTORIES[name] = factory


def get_factory(name: str) -> Any | None:
    """Return the registered factory for ``name``, or
    ``None`` if no connector type is registered for it."""
    return _FACTORIES.get(name)


def register_connector(
    connector: Connector,
    instance_id: str | None = None,
) -> None:
    """Manually install ``connector`` in the registry.

    ``instance_id`` defaults to the connector's
    ``_instance_id`` attribute, then ``"_default"``. The
    attribute is set on the connector object if missing
    so :func:`get_connector` finds it consistently.

    Used by tests and by connectors that aren't
    factory-built (e.g. an in-memory calendar stub).
    """
    if instance_id is None:
        instance_id = getattr(connector, "_instance_id", "_default")
    setattr(connector, "_instance_id", instance_id)
    key = (connector.name, instance_id)
    _INSTANCES[key] = connector


def get_connector(name: str, instance_id: str = "_default") -> Connector | None:
    """Return the constructed connector instance, or
    ``None`` when no instance with that key is loaded."""
    return _INSTANCES.get((name, instance_id))


def list_connectors() -> list[tuple[str, str]]:
    """All loaded ``(name, instance_id)`` pairs."""
    return list(_INSTANCES.keys())


async def load_connectors(configs: list[ConnectorConfig]) -> list[Connector]:
    """Construct + connect every enabled config.

    Idempotent: if a connector for the same key is
    already loaded it's disconnected and replaced (handles
    the operator adding a new instance mid-runtime).

    Skips configs whose ``name`` has no registered factory
    — the operator may have added a config for a connector
    that hasn't shipped yet. We log and move on.
    """
    loaded: list[Connector] = []
    for config in configs:
        if not config.enabled:
            continue
        factory = _FACTORIES.get(config.name)
        if factory is None:
            logger.warning(
                "connector type %r has no registered factory; "
                "skipping instance %r",
                config.name, config.instance_id,
            )
            continue
        key = (config.name, config.instance_id)
        previous = _INSTANCES.pop(key, None)
        if previous is not None:
            try:
                await previous.disconnect()
            except Exception:
                logger.exception(
                    "previous connector %r disconnect failed; "
                    "continuing with new instance", key,
                )

        try:
            connector = factory(config)
        except Exception:
            logger.exception(
                "connector factory %r failed for %r",
                config.name, config.instance_id,
            )
            continue

        # Tag the instance so :func:`get_connector` can find it
        # by ``(name, instance_id)``. ``Connector`` is a
        # :class:`Protocol` so we can't add an attribute to the
        # type; ``setattr`` on the concrete instance is the
        # least-invasive way.
        setattr(connector, "_instance_id", config.instance_id)
        _INSTANCES[key] = connector

        try:
            await connector.connect()
        except Exception:
            logger.exception(
                "connector %r connect failed; instance is "
                "loaded but in a non-running state", key,
            )

        loaded.append(connector)
    return loaded


async def unload_all() -> None:
    """Disconnect every loaded connector. Test + shutdown helper."""
    for key, connector in list(_INSTANCES.items()):
        try:
            await connector.disconnect()
        except Exception:
            logger.exception("connector %r disconnect failed", key)
    _INSTANCES.clear()


def reset() -> None:
    """Drop the registry entirely (test helper).

    Does NOT call ``disconnect`` on the loaded instances
    — call :func:`unload_all` first when you want a clean
    shutdown.
    """
    _INSTANCES.clear()
    _FACTORIES.clear()


__all__ = [
    "register_connector_factory",
    "get_factory",
    "register_connector",
    "get_connector",
    "list_connectors",
    "load_connectors",
    "unload_all",
    "reset",
]