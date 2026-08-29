"""SettingBook — local SQLite KV (system.timezone, tool_max_iterations, etc.).

Each row is a (key, value) string pair. Used for runtime-configurable
system settings. The schema mirrors the
``settings`` table.

The ``settings`` table also holds per-MAGI fields that used to live on
the ``magic`` row in the MAGIS schema — display ``name``,
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

import json
from dataclasses import dataclass

from sqlalchemy import Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.old_bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin

# Channel names are deliberately persisted strings, not a Python Enum.  A
# worker advertises its capability as it comes online, so a new adapter does
# not require a core-code vocabulary change before it can be selected.
CHANNEL_OPTIONS_KEY = "channels.available"

# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Setting(BaseRecord):
    key: str  # 配置键
    value: str  # 配置值（字符串）


# -- internal ORM --------------------------------------------------------


class _SettingRow(BaseRecordMixin):
    """ORM row for ``settings`` with ``key`` as a business unique key."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("key", name="uq_settings_key"), {"extend_existing": True})


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
        # ------------------------------------------------------------------
        # Per-MAGI runtime fields (formerly on the ``magic`` table).
        # ------------------------------------------------------------------
        # Operator-visible display name shown in the UI / API.
        "name",  # MAGI 的对外显示名
        # System prompt (soul) injected on every turn.
        "instruction",  # 注入到每轮对话的 system prompt
        # LLM provider slug, e.g. "openai" / "anthropic" / "deepseek".
        "provider",  # LLM 供应商标识（openai / anthropic / ...）
        # API key for the configured provider. Treat as a secret.
        "api_key",  # provider 对应的 API key（敏感字段）
        # Channel capabilities registered by BUS / workers at startup.
        CHANNEL_OPTIONS_KEY,
        # Operator-selected subset of ``channels.available``.
        "channels.enabled",
        # ------------------------------------------------------------------
        # System-level knobs.
        # ------------------------------------------------------------------
        # IANA timezone name used when rendering / scheduling. Defaults to "UTC".
        "system.timezone",  # 系统时区（IANA 名，默认 UTC）
        # Hard cap on tool-call iterations per agent run.
        "system.tool_max_iterations",  # 单次 Agent 调用的最大工具迭代次数
        # ------------------------------------------------------------------
        # Compaction policy (agent-worker-bus.md §6).
        # ------------------------------------------------------------------
        # Context window size (tokens) for the active model.
        "system.compact_context_window",  # 触发压缩的上下文窗口（token）
        # Percentage of the context window that triggers compaction.
        "system.compact_threshold_pct",  # 触发压缩的上下文占用百分比
        # Number of recent turns to keep verbatim after compaction.
        "system.compact_keep_recent",  # 压缩后保留的最近轮次数
    )

    model_cls = _SettingRow
    record_cls = Setting

    def get_value(self, *, key: str) -> str | None:
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
        return self.record_cls.from_row(row)

    def delete_by_key(self, *, key: str) -> bool:
        with self._session() as s:
            row = s.scalar(select(_SettingRow).where(_SettingRow.key == key))
            if row is None:
                return False
            record_id = row.id
        return self.delete(record_id)

    def list_keys(self) -> list[str]:
        with self._session() as s:
            rows = s.scalars(select(_SettingRow.key)).all()
            return list(rows)

    def list_all(self) -> list[Setting]:
        with self._session() as s:
            rows = s.scalars(select(_SettingRow).order_by(_SettingRow.key)).all()
            return [self.record_cls.from_row(r) for r in rows]

    def channel_options(self) -> list[str]:
        """Return registered channel names in stable registration order.

        Invalid historical JSON is treated as an empty registry.  This keeps
        startup recoverable; the owning BUS/worker will register its own name
        again on the same boot.
        """
        raw = self.get_value(key=CHANNEL_OPTIONS_KEY)
        try:
            values = json.loads(raw) if raw else []
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values:
            if isinstance(value, str) and value and value not in result:
                result.append(value)
        return result

    def register_channel(self, *, name: str) -> list[str]:
        """Idempotently advertise one BUS/worker-owned channel capability."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("channel name must be a non-empty string")
        name = name.strip()
        with self._session() as s:
            row = s.scalar(select(_SettingRow).where(_SettingRow.key == CHANNEL_OPTIONS_KEY))
            try:
                values = json.loads(row.value) if row is not None else []
            except (TypeError, json.JSONDecodeError):
                values = []
            options = [value for value in values if isinstance(value, str) and value]
            options = list(dict.fromkeys(options))
            if name not in options:
                options.append(name)
                if row is None:
                    s.add(_SettingRow(key=CHANNEL_OPTIONS_KEY, value=json.dumps(options)))
                else:
                    row.value = json.dumps(options)
                s.commit()
            return options

    def system_timezone(self) -> str:
        """Return the configured system timezone, defaulting to ``"UTC"``."""
        return self.get_value(key="system.timezone") or "UTC"


__all__ = [
    "CHANNEL_OPTIONS_KEY",
    "Setting",
    "SettingBook",
    "_SettingRow",
]
