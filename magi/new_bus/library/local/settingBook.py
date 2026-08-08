"""SettingBook — local SQLite KV (system.timezone, tool_max_iterations, etc.).

Each row is a (key, value) string pair. Used for runtime-configurable
system settings. The schema mirrors the old bus's
``magi.bus.db.models.local.setting.Setting`` table.

The ``settings`` table also holds per-MAGI fields that used to live on
the old ``magic`` row in the MAGIS schema — display ``name``,
``instruction``, LLM ``provider`` and ``api_key``.  Because each MAGI
only mutates its own state after bootstrap, that state belongs in the
LOCAL SQLite that the MAGI carries — not in the central MAGIS
PG/SQLite — and is keyed directly by name.

For the full inventory of keys the codebase actually uses, see
:attr:`SettingBook.KNOWN_KEYS`.  The book itself doesn't enforce the
list — callers may add arbitrary keys — but new code should add to
that tuple instead of inventing keys out of band.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    value: str
    updated_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _SettingRow(Base):
    __tablename__ = "settings"
    # ``setSettingNotify`` (in ``magi.new_bus.guild``) registers the
    # same Table for its fire-and-forget path; whichever module is
    # imported first wins, and the other must opt-in.
    __table_args__ = {"extend_existing": True}

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


# -- Book -----------------------------------------------------------------


class SettingBook(BaseBook[_SettingRow, Setting]):
    """Key/value store backed by the ``settings`` table.

    Provides basic CRUD over arbitrary keys.  Callers are responsible
    for the key vocabulary; this book does not enforce any schema.
    """

    #: Canonical inventory of every key the codebase reads or writes
    #: through this book, grouped by purpose.  New keys should be
    #: added here so the vocabulary stays in one place.  Per-MAGI
    #: fields moved here from the (now-removed) ``magic`` row in the
    #: MAGIS schema.
    KNOWN_KEYS: tuple[str, ...] = (
        # Per-MAGI runtime fields (formerly on the ``magic`` table).
        "name",
        "instruction",
        "provider",
        "api_key",
        # System-level knobs.
        "system.timezone",
        "system.tool_max_iterations",
        # Compaction policy (agent-worker-new-bus.md §6).
        "system.compact_context_window",
        "system.compact_threshold_pct",
        "system.compact_keep_recent",
        # Daily-note visibility (agent-worker-new-bus.md §6).
        "system.show_daily_note",
        "system.show_daily_note_prompt",
    )

    model_cls = _SettingRow
    dto_cls = Setting

    def get(self, *, key: str) -> str | None:
        with self._session() as s:
            row = s.scalar(select(_SettingRow).where(_SettingRow.key == key))
            return row.value if row else None

    def set(self, *, key: str, value: str) -> Setting:
        with self._session() as s:
            row = s.scalar(select(_SettingRow).where(_SettingRow.key == key))
            if row is None:
                row = _SettingRow(key=key, value=value)
                s.add(row)
            else:
                row.value = value
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def delete(self, *, key: str) -> bool:
        with self._session() as s:
            row = s.scalar(select(_SettingRow).where(_SettingRow.key == key))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    def list_keys(self) -> list[str]:
        with self._session() as s:
            rows = s.scalars(select(_SettingRow.key)).all()
            return list(rows)

    def list_all(self) -> list[Setting]:
        with self._session() as s:
            rows = s.scalars(select(_SettingRow).order_by(_SettingRow.key)).all()
            return [self._row_to_dto(r) for r in rows]

    # -- compaction / daily-note policy helpers (agent-worker-new-bus.md §6) ----

    #: Mirrors the old bus's :class:`magi.bus.jobs.services.setting.SettingService`.
    #: Bounds are deliberately permissive — the agent loop only needs sane
    #: defaults; the operator UI clamps inputs before persisting.
    DEFAULT_COMPACT_CONTEXT_WINDOW = 100000
    DEFAULT_COMPACT_THRESHOLD_PCT = 80
    DEFAULT_COMPACT_KEEP_RECENT = 20
    MIN_COMPACT_CONTEXT_WINDOW = 16000
    MAX_COMPACT_CONTEXT_WINDOW = 200000
    MIN_COMPACT_THRESHOLD_PCT = 50
    MAX_COMPACT_THRESHOLD_PCT = 95
    MIN_COMPACT_KEEP_RECENT = 5
    MAX_COMPACT_KEEP_RECENT = 100

    @staticmethod
    def _clamp_int(raw: str | None, *, default: int, lo: int, hi: int, label: str) -> int:
        if raw is None or raw == "":
            return default
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return default
        if v < lo or v > hi:
            return default
        return v

    @staticmethod
    def _read_bool(raw: str | None, *, default: bool) -> bool:
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")

    def compaction_policy(self) -> tuple[int, int, int]:
        """Return ``(context_window, threshold_pct, keep_recent)``.

        Mirrors ``SettingService.compaction_policy()`` in the old bus.
        Used by :func:`magi.agent.compaction.maybe_compact`.
        """
        return (
            self._clamp_int(
                self.get(key="system.compact_context_window"),
                default=self.DEFAULT_COMPACT_CONTEXT_WINDOW,
                lo=self.MIN_COMPACT_CONTEXT_WINDOW,
                hi=self.MAX_COMPACT_CONTEXT_WINDOW,
                label="context_window",
            ),
            self._clamp_int(
                self.get(key="system.compact_threshold_pct"),
                default=self.DEFAULT_COMPACT_THRESHOLD_PCT,
                lo=self.MIN_COMPACT_THRESHOLD_PCT,
                hi=self.MAX_COMPACT_THRESHOLD_PCT,
                label="threshold_pct",
            ),
            self._clamp_int(
                self.get(key="system.compact_keep_recent"),
                default=self.DEFAULT_COMPACT_KEEP_RECENT,
                lo=self.MIN_COMPACT_KEEP_RECENT,
                hi=self.MAX_COMPACT_KEEP_RECENT,
                label="keep_recent",
            ),
        )

    def show_daily_note(self) -> bool:
        return self._read_bool(
            self.get(key="system.show_daily_note"), default=True
        )

    def show_daily_note_prompt(self) -> bool:
        return self._read_bool(
            self.get(key="system.show_daily_note_prompt"), default=False
        )


__all__ = ["Setting", "SettingBook", "_SettingRow"]  # noqa: E501
