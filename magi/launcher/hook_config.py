"""Persistent hook plugin configuration.

The launcher owns the persistent config that drives whether a
hook plugin is loaded at runtime.  The WebUI Hooks knowledge
page and the ``magi hook`` CLI both write through this module;
the composition root reads it at boot to populate
``bus.hooks``.

Storage
-------

Each plugin row lives in the ``hook_plugin_configs`` table
(one row per installed plugin id).  The ``enabled`` boolean
controls whether the plugin is registered with the BUS at boot;
the row's remaining columns are the resolved
:class:`~magi.bus.hooks.contracts.HookRegistration` the loader
will install.

The WebUI writes here; the CLI writes here; nothing else
should.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.db.engine import open_session
from magi.bus.hooks.contracts import (
    HookDataScope,
    HookFailureMode,
    HookMode,
    HookPoint,
)
from magi.bus.hooks.hooks_service_init import HookConfigSource, HookPluginConfig


logger = logging.getLogger("magi.launcher.hook_config")


# ───────────────────────────────────────────────────────────────────── #
# ORM model
# ───────────────────────────────────────────────────────────────────── #


class HookPluginConfigRow(Base):
    __tablename__ = "hook_plugin_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hook_id: Mapped[str] = mapped_column(String(128), nullable=False)
    hook_version: Mapped[str] = mapped_column(String(32), nullable=False)
    module_path: Mapped[str] = mapped_column(String(256), nullable=False)
    class_name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    required_scopes: Mapped[list] = mapped_column(JSON, nullable=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    failure_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    hook_points: Mapped[list] = mapped_column(JSON, nullable=False)
    init_kwargs: Mapped[dict | None] = mapped_column("init_kwargs_json", JSON, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive,
    )

    __table_args__ = (
        UniqueConstraint("hook_id", name="uq_hook_plugin_configs_hook_id"),
    )


# ───────────────────────────────────────────────────────────────────── #
# Repository
# ───────────────────────────────────────────────────────────────────── #


class HookConfigRepository(HookConfigSource):
    """Persistent hook config CRUD.

    The class implements :class:`HookConfigSource` so the
    composition root's ``install_hooks_into_bus`` helper can
    consume it directly.  The WebUI / CLI use the same instance.
    """

    def __init__(self, state_dir: str | None = None) -> None:
        self._state_dir = state_dir

    # -- read ---------------------------------------------------------- #

    def list_enabled(self) -> Iterable[HookPluginConfig]:
        for entry in self.list_all():
            if entry.enabled:
                yield entry

    def list_all(self) -> tuple[HookPluginConfig, ...]:
        with open_session(self._state_dir) as session:
            rows = list(
                session.query(HookPluginConfigRow)
                .order_by(HookPluginConfigRow.priority.asc())
                .all()
            )
            return tuple(_row_to_config(r) for r in rows)

    def get(self, hook_id: str) -> HookPluginConfig | None:
        with open_session(self._state_dir) as session:
            row = session.query(HookPluginConfigRow).filter_by(hook_id=hook_id).first()
            return _row_to_config(row) if row is not None else None

    # -- write --------------------------------------------------------- #

    def install(
        self,
        *,
        hook_id: str,
        hook_version: str,
        module_path: str,
        class_name: str,
        hook_points: tuple[HookPoint, ...],
        mode: HookMode,
        priority: int,
        required_scopes: frozenset[HookDataScope],
        timeout_ms: int,
        failure_mode: HookFailureMode,
        init_kwargs: Mapping[str, Any] | None = None,
        enabled: bool = True,
    ) -> HookPluginConfig:
        """Insert or update one plugin config row.

        Idempotent on ``hook_id`` — installing the same plugin
        twice updates the row in place rather than failing on the
        unique constraint.
        """
        with open_session(self._state_dir) as session:
            existing = session.query(HookPluginConfigRow).filter_by(hook_id=hook_id).first()
            if existing is not None:
                existing.hook_version = hook_version
                existing.module_path = module_path
                existing.class_name = class_name
                existing.mode = mode.value
                existing.priority = priority
                existing.required_scopes = [s.value for s in required_scopes]
                existing.timeout_ms = timeout_ms
                existing.failure_mode = failure_mode.value
                existing.hook_points = [p.value for p in hook_points]
                existing.init_kwargs = dict(init_kwargs or {})
                existing.enabled = enabled
                session.commit()
                return _row_to_config(existing)
            row = HookPluginConfigRow(
                hook_id=hook_id,
                hook_version=hook_version,
                module_path=module_path,
                class_name=class_name,
                enabled=enabled,
                mode=mode.value,
                priority=priority,
                required_scopes=[s.value for s in required_scopes],
                timeout_ms=timeout_ms,
                failure_mode=failure_mode.value,
                hook_points=[p.value for p in hook_points],
                init_kwargs=dict(init_kwargs or {}),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _row_to_config(row)

    def set_enabled(self, hook_id: str, enabled: bool) -> bool:
        """Flip the ``enabled`` flag for ``hook_id``.

        Returns ``True`` if the row was updated, ``False`` if
        the hook id is unknown.
        """
        with open_session(self._state_dir) as session:
            row = session.query(HookPluginConfigRow).filter_by(hook_id=hook_id).first()
            if row is None:
                return False
            row.enabled = enabled
            row.updated_at = utcnow_naive()
            session.commit()
            return True

    def uninstall(self, hook_id: str) -> bool:
        """Delete the row for ``hook_id``.

        Returns ``True`` if the row was deleted, ``False`` if
        the hook id is unknown.
        """
        with open_session(self._state_dir) as session:
            row = session.query(HookPluginConfigRow).filter_by(hook_id=hook_id).first()
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True


# ───────────────────────────────────────────────────────────────────── #
# Composition-root loader
# ───────────────────────────────────────────────────────────────────── #


def load_hook_config_into_bus(state_dir: str) -> HookConfigSource:
    """Build a :class:`HookConfigSource` for ``state_dir``.

    A thin convenience wrapper the composition root uses so the
    call site in :mod:`magi.__main__` doesn't need to know the
    repository class.
    """
    return HookConfigRepository(state_dir=state_dir)


# ───────────────────────────────────────────────────────────────────── #
# Row → config conversion
# ───────────────────────────────────────────────────────────────────── #


def _row_to_config(row: HookPluginConfigRow) -> HookPluginConfig:
    return HookPluginConfig(
        hook_id=row.hook_id,
        hook_version=row.hook_version,
        module_path=row.module_path,
        class_name=row.class_name,
        enabled=bool(row.enabled),
        mode=HookMode(row.mode),
        priority=int(row.priority),
        required_scopes=frozenset(HookDataScope(s) for s in (row.required_scopes or [])),
        timeout_ms=int(row.timeout_ms),
        failure_mode=HookFailureMode(row.failure_mode),
        init_kwargs=dict(row.init_kwargs or {}),
    )


__all__ = [
    "HookConfigRepository",
    "HookPluginConfigRow",
    "load_hook_config_into_bus",
]


# Silence unused-import lint.
_ = (datetime, Any)
