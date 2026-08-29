"""runToolJobBoard — 工具执行作业。

worker claim → 执行工具 → submit_result

Result shape mirrors :class:`CallLLMResult`: ``error`` /
``error_code`` are the failure pair (human-readable +
machine-stable), ``content`` is the :class:`ToolResult` text the
executable ``Tool.run()`` returns (rendered for the LLM).
``tool_call_id`` round-trips with :class:`RunToolJob` so the agent
can correlate a finished tool call with the LLM turn that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.old_bus.bases.db.base import enum_column
from magi.old_bus.bases.job import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin

# -- public enum -----------------------------------------------------------


class ToolErrorCode(StrEnum):
    """Stable error code returned on a failed :class:`RunToolResult`.

    ``NONE`` 表示成功（无错误）；其余成员是工具层的稳定错误码。
    Mirrors :class:`magi.bus.firmwares.jobs.callLLMJob.LLMErrorCode`。
    """

    NONE = ""  # 成功（对齐 LLMErrorCode.NONE）
    UNKNOWN = "tool.unknown"  # 工具不存在（catalog 查不到）
    CRASHED = "tool.crashed"  # 工具执行抛异常（真实 bug）
    CANCELLED = "tool.cancelled"  # worker 取消
    UNAUTHORIZED = "tool.unauthorized"  # 角色门控拒绝
    FAILED = "tool.failed"  # 工具层预期失败（ToolResult.err，无稳定码）


@dataclass(frozen=True, slots=True)
class RunToolJob(BaseJob):
    """一个工具执行 job。

    ``tool_name`` + ``payload`` 是执行目标与参数；``tool_call_id``
    关联产生这次调用的 LLM ``tool_use.id``，回执用它反哺 conversation。

    Catalog 过期校验（``catalog_revision`` / ``schema_hash``）已移除：
    schema 不一致时工具执行本身会失败并回传错误，无需在 claim 侧
    预先比对。
    """

    tool_name: str  # 目标 tool 名（与 catalog.tool_name 对齐）
    payload: dict  # tool 调用参数（按该 tool 的 args schema 校验）
    tool_call_id: str = ""  # 关联的 LLM tool_use.id；回执用它反哺 conversation


@dataclass(frozen=True, slots=True)
class RunToolResult(BaseJobResult):
    """工具执行的完成结果。

    ``content`` 是 :class:`ToolResult` 的纯文本内容（给 LLM 看）；
    成败由 ``error_code`` 表达——:attr:`ToolErrorCode.NONE` 表示成功，
    其余成员表示失败（对齐 :class:`CallLLMResult` 的
    :class:`~magi.bus.firmwares.jobs.callLLMJob.LLMErrorCode` 模式）。基类的
    ``error`` 是失败时的人类可读文案。
    """

    content: str = ""  # ToolResult 的纯文本内容（worker 截断到 8 KB）
    error_code: ToolErrorCode = ToolErrorCode.NONE  # 稳定错误码（成功 = NONE）
    tool_call_id: str = ""  # 回传对应 RunToolJob.tool_call_id，方便 caller 反哺 LLM tool_result


class _ToolJobRow(BaseJobRowMixin):
    __tablename__ = "tool_jobs"
    __table_args__ = {"extend_existing": True}

    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    tool_call_id: Mapped[str] = mapped_column(Text, default="")

    # -- result-side columns (aligned with RunToolResult) -----------------
    # ``content`` stores the plain-text ToolResult.content (truncated to
    # 8 KB by the worker) — the only return payload, rendered for the LLM.
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[ToolErrorCode] = mapped_column(
        enum_column(ToolErrorCode), nullable=False, default=ToolErrorCode.NONE
    )


class runToolJobBoard(BaseJobBoard[_ToolJobRow, RunToolJob, RunToolResult]):
    job_model = _ToolJobRow
    job_cls = RunToolJob
    result_cls = RunToolResult
