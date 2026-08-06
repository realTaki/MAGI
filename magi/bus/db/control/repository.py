"""Backwards-compatibility shim — moved to :mod:`magi.bus.db.repositories.magis.control`.

The :class:`ControlRepository` now lives under
``magi/bus/db/repositories/magis/control.py`` (one of the Repository
classes that will eventually host every domain's data-access layer).
This file remains so existing ``from magi.bus.db.control.repository
import …`` imports keep working; new code should import from the
canonical location.
"""

from magi.bus.db.repositories.magis.control import (  # noqa: F401
    ControlRepository,
    PortAllocationDTO,
    PortAlreadyAllocated,
    RuntimeStateDTO,
    UnknownRuntime,
)

__all__ = [
    "ControlRepository",
    "PortAllocationDTO",
    "PortAlreadyAllocated",
    "RuntimeStateDTO",
    "UnknownRuntime",
]
