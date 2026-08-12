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

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.library.base import BaseBook

# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Setting:
    key: str  # 配置键
    value: str  # 配置值（字符串）
    updated_at: datetime | None = None  # 最近更新时间


# -- internal ORM --------------------------------------------------------


class _SettingRow(Base):
    __tablename__ = "settings"
    # ``setSettingNotify`` (in ``magi.bus.guild``) registers the
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
        # ------------------------------------------------------------------
        # Daily-note visibility (agent-worker-bus.md §6).
        # ------------------------------------------------------------------
        # Whether the agent reads its daily note on every turn.
        "system.show_daily_note",  # 是否在每轮注入 daily note
        # Whether the operator wants a prompt to update the daily note.
        "system.show_daily_note_prompt",  # 是否在每轮提示更新 daily note
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

    @staticmethod
    def _read_bool(raw: str | None, *, default: bool) -> bool:
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")

    def mcp_timeout_config(
        self,
        *,
        connect_default: float = 10.0,
        execute_default: float = 60.0,
        sse_default: float = 120.0,
    ) -> MCPTimeout:
        """Read the three MCP timeout settings with type coercion.

        Each key is read as a string, coerced to ``float``, and
        falls back to its default on missing / unparseable values.
        """
        return MCPTimeout(
            connect_timeout=self._read_float(
                self.get(key="mcp.connect_timeout"),
                connect_default,
            ),
            execute_timeout=self._read_float(
                self.get(key="mcp.execute_timeout"),
                execute_default,
            ),
            sse_read_timeout=self._read_float(
                self.get(key="mcp.sse_read_timeout"),
                sse_default,
            ),
        )

    @staticmethod
    def _read_float(raw: str | None, default: float) -> float:
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    def show_daily_note(self) -> bool:
        return self._read_bool(self.get(key="system.show_daily_note"), default=True)

    def show_daily_note_prompt(self) -> bool:
        return self._read_bool(self.get(key="system.show_daily_note_prompt"), default=False)

    def system_timezone(self) -> str:
        """Return the configured system timezone, defaulting to ``"UTC"``."""
        return self.get(key="system.timezone") or "UTC"


@dataclass(frozen=True, slots=True)
class MCPTimeout:
    """The three MCP connection timeout knobs."""

    connect_timeout: float = 10.0  # 建立连接的超时（秒）
    execute_timeout: float = 60.0  # 工具调用执行的超时（秒）
    sse_read_timeout: float = 120.0  # SSE 流读取的超时（秒）


__all__ = ["Setting", "SettingBook", "MCPTimeout", "_SettingRow"]  # noqa: E501
