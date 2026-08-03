"""BUS DTOs for persisted connector configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConnectorConfiguration:
    """One connector configuration consumed by the connector worker."""

    name: str
    instance_id: str
    enabled: bool
    settings: dict[str, object]
    auth: dict[str, object] | None
