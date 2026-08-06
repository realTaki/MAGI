"""BUS facade for the control-plane runtime registry.

Wrap :class:`magi.bus.db.control.repository.ControlRepository` so
business modules see a stable façade on ``bus.control_registry.*``
rather than reaching into ``magi.bus.db.control.*`` directly.

The repository is backed by the same MAGIS engine that holds
organisation facts — no separate ``control/`` database.
"""

from __future__ import annotations

import logging
from typing import Optional

from magi.bus.db.control.repository import (
    ControlRepository,
    PortAllocationDTO,
    PortAlreadyAllocated,
    RuntimeStateDTO,
    UnknownRuntime,
)

# Re-export the desired / observed state enums so consumers can import
# them via the BUS façade rather than reaching into ``magi.bus.db.models``.
from magi.bus.db.models.local.control_runtime import (  # noqa: E402
    RuntimeDesiredState,
    RuntimeObservedState,
)

logger = logging.getLogger("magi.bus.jobs.services.control_registry")


class ControlRegistryService:
    """BUS-side wrapper around :class:`ControlRepository`."""

    def __init__(self, repository: ControlRepository) -> None:
        self._repo = repository

    @property
    def repository(self) -> ControlRepository:
        """Tests may need direct access; production code should not."""
        return self._repo

    # -- runtime lifecycle commands ----------------------------------------

    def upsert_desired_state(self, runtime_id: int, backend_kind: str, desired) -> None:
        self._repo.upsert_desired_state(runtime_id, backend_kind, desired)

    def attach_paths(self, runtime_id: int, workspace_dir, log_dir, audit_log_path, backend_ref: str) -> None:
        self._repo.attach_paths(runtime_id, workspace_dir, log_dir, audit_log_path, backend_ref)

    def record_spawn(self, runtime_id: int, pid: int, base_url: str, port: int) -> None:
        self._repo.record_spawn(runtime_id, pid, base_url, port)

    def record_observed(self, runtime_id: int, observed) -> None:
        self._repo.record_observed(runtime_id, observed)

    def record_stop(self, runtime_id: int) -> None:
        self._repo.record_stop(runtime_id)

    def archive_workspace(self, runtime_id: int, archive_path) -> None:
        self._repo.archive_workspace(runtime_id, archive_path)

    def forget(self, runtime_id: int) -> None:
        self._repo.forget(runtime_id)

    def mark_stale(self, runtime_id: int, stale: bool = True) -> None:
        self._repo.mark_stale(runtime_id, stale)

    # -- queries -----------------------------------------------------------

    def list_runtimes(self) -> list[RuntimeStateDTO]:
        return self._repo.list_runtimes()

    def get_runtime(self, runtime_id: int) -> RuntimeStateDTO:
        return self._repo.get_runtime(runtime_id)

    def list_stale(self) -> list[RuntimeStateDTO]:
        return self._repo.list_stale()

    def allocate_port(self, runtime_id: int) -> PortAllocationDTO:
        try:
            return self._repo.allocate_port(runtime_id)
        except RuntimeError as exc:
            raise PortAlreadyAllocated(-1) from exc

    def release_port(self, runtime_id: int) -> None:
        self._repo.release_port(runtime_id)

    # -- secrets -----------------------------------------------------------

    def put_secret(self, name: str, raw: str) -> None:
        self._repo.put_secret(name, raw)

    def verify_secret(self, name: str, raw: str) -> bool:
        return self._repo.verify_secret(name, raw)


__all__ = ["ControlRegistryService", "UnknownRuntime"]
