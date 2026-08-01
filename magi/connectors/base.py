"""Connector protocol + event types.

The :class:`Connector` protocol is what every concrete connector
(Gmail / Calendar / Linear / …) implements. It is small on
purpose: ``connect`` + ``disconnect`` + ``fetch`` + ``name`` +
``config``. The plugin bus is where the action is — connectors
*emit* events, plugins + tool wrappers *consume* them.

Concrete subclass example lives in
:mod:`magi.connectors.samples.calendar`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


# -- Event kinds ------------------------------------------------------------


class ConnectorEventKind(str, enum.Enum):
    """Coarse taxonomy of connector events.

    Subscribers pattern-match on ``kind``. New kinds can be added
    freely — they're stable for the lifetime of a connector
    instance, not the lifetime of the codebase.
    """

    DATA = "data"
    """Generic data payload — ``payload`` is whatever the
    connector wants to surface."""

    CREATED = "created"
    """A new upstream object appeared."""

    UPDATED = "updated"
    """An existing upstream object changed."""

    DELETED = "deleted"
    """An upstream object was removed."""

    ERROR = "error"
    """The connector hit a transient upstream failure. The
    handler decides whether to retry, log, or notify the
    operator."""

    LIFECYCLE = "lifecycle"
    """Connection-state events (connected / disconnected /
    throttled). Tools may surface these to the operator."""


# -- Event payload ----------------------------------------------------------


@dataclass(frozen=True)
class ConnectorEvent:
    """One connector-emitted event.

    ``id`` is unique per connector instance — the bus uses it
    for de-duplication when a connector redelivers the same
    event after a reconnect.

    ``kind`` is the coarse taxonomy; ``payload`` is the
    connector-specific shape (Gmail message id, Calendar
    event id, Linear issue id, …).

    ``occurred_at`` is the upstream-side wall-clock time when
    the event happened; ``received_at`` is when this process
    saw it. The two can drift on reconnects.
    """

    connector: str
    kind: ConnectorEventKind
    id: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None
    received_at: datetime = field(default_factory=datetime.utcnow)

    def key(self) -> tuple[str, str]:
        """Dedup key: ``(connector, id)``."""
        return (self.connector, self.id)


# -- Handler protocol -------------------------------------------------------


class EventHandler(Protocol):
    """Coroutine that handles a :class:`ConnectorEvent`.

    Implementations MUST NOT raise; the bus catches and
    logs but does not retry. Handlers that need retry
    semantics do their own bookkeeping.
    """

    async def __call__(self, event: ConnectorEvent) -> None: ...


# -- Connector config -------------------------------------------------------


@dataclass(frozen=True)
class ConnectorConfig:
    """One connector instance configuration.

    Multiple instances of the same connector type are allowed —
    e.g. two Gmail accounts for two operators. ``instance_id``
    scopes the events to one specific instance.
    """

    name: str
    """Connector type id, e.g. ``"gmail"`` / ``"calendar"`` /
    ``"linear"``. Stable across releases."""

    instance_id: str
    """Per-instance id, e.g. ``"user_a"`` / ``"team_calendar"``.
    Combined with ``name`` to form the unique key in the
    registry."""

    enabled: bool = True
    """Operator toggle. Disabled configs are skipped at
    boot and ignored by ``load_connectors``."""

    settings: dict[str, Any] = field(default_factory=dict)
    """Connector-type-specific configuration (calendar URL,
    IMAP host, webhook path, …). Free-form; the connector
    class validates at connect time."""

    auth: dict[str, str] = field(default_factory=dict)
    """Auth material (OAuth tokens, API keys, basic-auth
    user/password). Stored encrypted-at-rest by the WebUI
    settings surface; the connector sees them as a plain
    dict at connect time."""

    def key(self) -> tuple[str, str]:
        return (self.name, self.instance_id)


# -- Connector protocol ----------------------------------------------------


@runtime_checkable
class Connector(Protocol):
    """The contract every concrete connector implements.

    Lifecycle:
      1. Operator adds a :class:`ConnectorConfig` to the DB.
      2. ``load_connectors()`` constructs one connector per
         enabled config and calls ``await connector.connect()``.
      3. The connector opens upstream sockets, fetches
         initial state, and emits events for any deltas.
      4. On shutdown, ``unload_all()`` calls
         ``await connector.disconnect()`` on every instance.

    Threading: every method is async. Connectors run on the
    runtime's asyncio loop. Long-running background work
    (polling, webhook listeners) lives in tasks the connector
    spawns in ``connect()`` and cancels in ``disconnect()``.
    """

    @property
    def name(self) -> str:
        """Type id, e.g. ``"calendar"``. Matches the
        ``name`` field on the config the runtime loaded
        this instance from."""
        ...

    async def connect(self) -> None:
        """Open upstream, fetch initial state, start emitting.

        Idempotent: a second call after a successful connect
        is a no-op. The implementation MUST NOT raise on
        transient upstream failures — instead emit an
        ``ERROR`` event and let the operator decide.
        """
        ...

    async def disconnect(self) -> None:
        """Cancel background tasks, close sockets, flush state.

        Called at runtime shutdown. Idempotent."""
        ...

    async def fetch(
        self,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        """Pull-style query — for LLM-side tool wrappers.

        ``query`` shape is connector-specific. Examples:
        ``calendar.fetch({"from": iso, "to": iso})`` →
        ``{"events": [...]}``;
        ``gmail.fetch({"q": "from:foo", "limit": 10})`` →
        ``{"messages": [...]}``.

        Implementations MUST return a JSON-serialisable dict.
        Empty / not-found → ``{"items": []}`` (or the
        connector-specific key) so the LLM doesn't trip on
        ``None``.
        """
        ...


__all__ = [
    "ConnectorEventKind",
    "ConnectorEvent",
    "EventHandler",
    "ConnectorConfig",
    "Connector",
]