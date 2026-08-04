"""Local Profile port allocator — sticky by ``runtime_id``.

Per plan §7.2 the Local Profile assigns each runtime a port in the
fixed range ``42101-42999`` and **sticks** the assignment across
stop/start.  A port is released only when the runtime is fully
deleted (per plan §7.4).

The repository :meth:`ControlRepository.allocate_port` is the
authoritative source.  This module is a thin helper that handles the
case where the supervisor wants to ask "is port X already held?" or
"find the lowest-free one" without crossing the SQL boundary.
"""

from __future__ import annotations

from typing import Optional

from magi.bus.services.control_registry import ControlRegistryService
from magi.bus.db.control.repository import PortAllocationDTO, RuntimeStateDTO


def reserve_port(
    control: ControlRegistryService,
    runtime_id: int,
) -> PortAllocationDTO:
    """Reserve or re-allocate a port for ``runtime_id`` (sticky across stop)."""
    return control.allocate_port(runtime_id)


def release_port(control: ControlRegistryService, runtime_id: int) -> None:
    """Release the runtime's port reservation.

    Called by ``LocalProcessRuntimeBackend.delete`` only — per plan
    §7.4 ``stop`` keeps the assignment, only ``delete`` releases it.
    """
    control.release_port(runtime_id)


def port_for(control: ControlRegistryService, runtime_id: int) -> Optional[int]:
    """Return the runtime's currently-reserved port, if any."""
    try:
        return control.get_runtime(runtime_id).port
    except Exception:
        return None


__all__ = ["reserve_port", "release_port", "port_for", "PortAllocationDTO", "RuntimeStateDTO"]
