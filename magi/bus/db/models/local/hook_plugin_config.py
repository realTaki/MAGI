"""Persistent plugin enablement config (relocated from launcher.hook_config).



The OLD hook subsystem (HookService + HookEnvelope + GATE/OBSERVE
inline handlers) is replaced by a tag-based design:

  - The ``hook_plugin_configs`` table is now a **plugin enablement**
    table.  Each row records one plugin's id, the hook points it
    subscribes to (JSON list of strings), and the boolean flag
    that controls whether the plugin worker is allowed to consume
    signoffs.

  - The composition root (this module's readers) does NOT register
    handlers anymore; plugin workers query this table at boot to
    learn what hook points they should listen to.  When
    ``bus.store.enqueue_llm_job`` (and friends) writes a row, it
    reads the same table to decide which plugins owe a signoff.

The legacy ``mode``, ``priority``, ``required_scopes``,
``timeout_ms``, ``failure_mode``, ``init_kwargs`` columns are
left in the schema for downgrade safety -- no new code reads
them.  A future migration will trim them.

The WebUI writes here; the CLI writes here; nothing else
should.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.db.engine import open_session


logger = logging.getLogger("magi.bus.db.models.local.hook_plugin_config")


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
    # Legacy columns retained for downgrade safety; new code does
    # not read them.  See module docstring.
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
# Minimal in-memory dataclass (the legacy ``HookPluginConfig`` had
# 10 fields; the new design only cares about ``hook_id``,
# ``enabled`` and ``hook_points``.  Keep the dataclass small so
# plugin workers have a clean contract.)
# ───────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class HookPluginConfig:
    """One plugin's enablement record.

    ``hook_id`` is the stable plugin identifier (matches the
    ``hook_signoffs.plugin_id`` column).  ``hook_points`` is the
    JSON list of hook-point strings the plugin subscribes to --
    used by ``bus.store._dispatch_hook_signoffs`` to decide which
    plugin workers owe a signoff on a given durable row.
    """

    hook_id: str
    enabled: bool
    hook_points: tuple[str, ...]


# ───────────────────────────────────────────────────────────────────── #
# Repository
# ───────────────────────────────────────────────────────────────────── #


class HookConfigRepository:
    """Persistent plugin enablement CRUD.

    Replaces the OLD :class:`HookConfigSource` interface that
    coupled this module to ``magi.bus.hooks.install_hooks_into_bus``.
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
                session.execute(
                    select(HookPluginConfigRow).order_by(
                        HookPluginConfigRow.priority.asc()
                    )
                ).scalars()
            )
            return tuple(_row_to_config(r) for r in rows)

    def get(self, hook_id: str) -> HookPluginConfig | None:
        with open_session(self._state_dir) as session:
            row = session.execute(
                select(HookPluginConfigRow).where(HookPluginConfigRow.hook_id == hook_id)
            ).scalar_one_or_none()
            return _row_to_config(row) if row is not None else None

    def get_enabled(self, hook_id: str) -> HookPluginConfig | None:
        """Return the config only if ``enabled`` is true; else ``None``."""
        entry = self.get(hook_id)
        return entry if entry is not None and entry.enabled else None

    # -- write --------------------------------------------------------- #

    def install(
        self,
        *,
        hook_id: str,
        hook_version: str,
        module_path: str,
        class_name: str,
        hook_points: Iterable[str],
        enabled: bool = True,
    ) -> HookPluginConfig:
        """Insert or update one plugin enablement row.

        Idempotent on ``hook_id``.  Only the columns the new
        design cares about (``hook_id``, ``hook_version``,
        ``module_path``, ``class_name``, ``hook_points``,
        ``enabled``) are written; the legacy columns are left at
        their defaults so the row still type-checks under the old
        schema.
        """
        hook_points_list = list(hook_points)
        with open_session(self._state_dir) as session:
            existing = session.execute(
                select(HookPluginConfigRow).where(HookPluginConfigRow.hook_id == hook_id)
            ).scalar_one_or_none()
            if existing is not None:
                existing.hook_version = hook_version
                existing.module_path = module_path
                existing.class_name = class_name
                existing.hook_points = hook_points_list
                existing.enabled = enabled
                session.commit()
                return _row_to_config(existing)
            row = HookPluginConfigRow(
                hook_id=hook_id,
                hook_version=hook_version,
                module_path=module_path,
                class_name=class_name,
                enabled=enabled,
                mode="observe",
                priority=100,
                required_scopes=[],
                timeout_ms=500,
                failure_mode="fail_open",
                hook_points=hook_points_list,
                init_kwargs={},
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _row_to_config(row)

    def set_enabled(self, hook_id: str, enabled: bool) -> bool:
        """Flip the ``enabled`` flag for ``hook_id``."""
        with open_session(self._state_dir) as session:
            row = session.execute(
                select(HookPluginConfigRow).where(HookPluginConfigRow.hook_id == hook_id)
            ).scalar_one_or_none()
            if row is None:
                return False
            row.enabled = enabled
            row.updated_at = utcnow_naive()
            session.commit()
            return True

    def uninstall(self, hook_id: str) -> bool:
        """Delete the row for ``hook_id``."""
        with open_session(self._state_dir) as session:
            row = session.execute(
                select(HookPluginConfigRow).where(HookPluginConfigRow.hook_id == hook_id)
            ).scalar_one_or_none()
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True


# ───────────────────────────────────────────────────────────────────── #
# Row → config conversion
# ───────────────────────────────────────────────────────────────────── #


def _row_to_config(row: HookPluginConfigRow) -> HookPluginConfig:
    return HookPluginConfig(
        hook_id=row.hook_id,
        enabled=bool(row.enabled),
        hook_points=tuple(row.hook_points or ()),
    )


__all__ = [
    "HookConfigRepository",
    "HookPluginConfig",
    "HookPluginConfigRow",
]