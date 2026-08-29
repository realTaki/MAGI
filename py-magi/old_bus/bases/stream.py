"""StreamHub — light-weight in-process pipe registry.

Producers (e.g. provider worker) push streaming deltas into a named
pipe; consumers (e.g. agent worker) pull from the same pipe.  No
DB writes — only a string key crosses the job board.

Usage
=====

Producer::

    stream = bus.stream_hub.create("abc123")
    stream.put("你")
    stream.put("好")
    stream.put(None)   # sentinel — consumer knows it's done

Consumer::

    stream = bus.stream_hub.get("abc123")
    if stream is None:
        return  # stream already closed
    while True:
        chunk = await stream.get()
        if chunk is None:
            break
        ...

Lifecycle
---------

- ``create(key)`` — register a new pipe (replaces existing).
- ``get(key)`` — get a pipe for reading (``None`` if unknown).
- ``close(key)`` — remove from registry after producer is done.

Design note
-----------

Placed in ``bus.bases`` so that neither producer nor consumer
needs to import ``providers``.  Both sides talk to
``bus.stream_hub`` exclusively.
"""

from __future__ import annotations

import asyncio
from typing import Any


class StreamHub:
    """In-memory named-pipe registry backed by asyncio.Queue."""

    def __init__(self) -> None:
        self._pipes: dict[str, asyncio.Queue[Any]] = {}

    def create(self, key: str) -> asyncio.Queue[Any]:
        """Register a new pipe and return its write-end."""
        q: asyncio.Queue[Any] = asyncio.Queue()
        self._pipes[key] = q
        return q

    def get(self, key: str) -> asyncio.Queue[Any] | None:
        """Return the read-end of an existing pipe, or ``None``."""
        return self._pipes.get(key)

    def close(self, key: str) -> None:
        """Remove a pipe from the registry."""
        self._pipes.pop(key, None)
