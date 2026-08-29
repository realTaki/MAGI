"""Read-only control capability for the singleton WebUI process.

The WebUI exposes the operator-facing port and proxies target-specific
operations to MAGI runtimes.  It does not need any node storage or
per-channel workers; the only Bus capability it actually consumes is
the shared control/MAGIS store, packaged here so :mod:`channels`
owns its own DI surface and the startup layer no longer leaks a
context type back into the channel package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # import-only — kept as a string for cycle safety
    from old_bus import MagisBus


@dataclass(frozen=True, slots=True)
class ControlContext:
    """Read/open-only control capability for the singleton WebUI process."""

    bus: "MagisBus"


__all__ = ["ControlContext"]
