"""BUS-owned persistence reads for connector worker configuration."""

from __future__ import annotations

import json

from sqlalchemy import text

from magi.bus.contracts.connector import ConnectorConfiguration


class ConnectorService:
    """Read durable connector configs without importing connector implementations."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def list_configurations(self) -> list[ConnectorConfiguration] | None:
        """Return configs, or ``None`` while the optional table is absent."""
        from magi.db.engine import get_engine

        try:
            with get_engine().connect() as connection:
                exists = connection.execute(text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='connector_configs'"
                )).fetchone()
                if exists is None:
                    return None
                rows = connection.execute(text(
                    "SELECT name, instance_id, enabled, settings_json, auth_json "
                    "FROM connector_configs"
                )).fetchall()
        except Exception:
            return None

        configs: list[ConnectorConfiguration] = []
        for name, instance_id, enabled, settings_json, auth_json in rows:
            try:
                settings = json.loads(settings_json) if settings_json else {}
                auth = json.loads(auth_json) if auth_json else None
            except (TypeError, ValueError):
                continue
            if not isinstance(settings, dict) or (auth is not None and not isinstance(auth, dict)):
                continue
            configs.append(ConnectorConfiguration(
                name=str(name), instance_id=str(instance_id), enabled=bool(enabled),
                settings=settings, auth=auth,
            ))
        return configs
