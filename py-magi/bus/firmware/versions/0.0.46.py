"""Make LLM Jobs use MAGI's backend-neutral message contract.

Revision ID: 0.0.46
Revises: 0.0.45
Create Date: 2026-08-31
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.46"
down_revision = "0.0.45"
branch_labels = None
depends_on = None

_TABLE = "jobs_call_llm"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        blocks = item.get("content_blocks")
        if role == "assistant":
            calls: list[dict[str, Any]] = []
            text_parts: list[str] = []
            if isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(_text(block.get("text")))
                    elif block.get("type") == "tool_use":
                        calls.append(
                            {
                                "id": _text(block.get("id")),
                                "name": _text(block.get("name")),
                                "arguments": _arguments(block.get("input")),
                            }
                        )
            out.append(
                {
                    "role": "assistant",
                    "text": _text(item.get("content")) or "\n".join(text_parts),
                    "tool_calls": calls,
                    "tool_call_id": None,
                    "is_error": False,
                }
            )
            continue
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_call_id = _text(block.get("tool_use_id") or block.get("id"))
                if not tool_call_id:
                    continue
                out.append(
                    {
                        "role": "tool",
                        "text": _text(block.get("content")),
                        "tool_calls": [],
                        "tool_call_id": tool_call_id,
                        "is_error": bool(block.get("is_error")),
                    }
                )
        text = _text(item.get("content"))
        if text or not blocks:
            out.append(
                {
                    "role": "system" if role == "system" else "user",
                    "text": text,
                    "tool_calls": [],
                    "tool_call_id": None,
                    "is_error": False,
                }
            )
    return out


def _tools(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for tool in value:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = function.get("name")
        if not name:
            continue
        out.append(
            {
                "name": _text(name),
                "description": _text(function.get("description")),
                "input_schema": function.get("parameters") or function.get("input_schema") or {},
            }
        )
    return out


def _result_message(text: Any, tool_uses: Any) -> dict[str, Any] | None:
    calls: list[dict[str, Any]] = []
    if isinstance(tool_uses, list):
        for call in tool_uses:
            if not isinstance(call, dict):
                continue
            calls.append(
                {
                    "id": _text(call.get("id")),
                    "name": _text(call.get("name")),
                    "arguments": _arguments(call.get("input")),
                }
            )
    if text is None and not calls:
        return None
    return {
        "role": "assistant",
        "text": _text(text),
        "tool_calls": calls,
        "tool_call_id": None,
        "is_error": False,
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}

    with op.batch_alter_table(_TABLE) as batch:
        if "message" not in columns:
            batch.add_column(sa.Column("message", sa.JSON(), nullable=True))
        if "usage" not in columns:
            batch.add_column(sa.Column("usage", sa.JSON(), nullable=True))
        if "max_tokens" in columns and "max_output_tokens" not in columns:
            batch.alter_column("max_tokens", new_column_name="max_output_tokens", existing_type=sa.Integer())

    jobs = sa.table(
        _TABLE,
        sa.column("id", sa.Integer()),
        sa.column("messages", sa.JSON()),
        sa.column("tools", sa.JSON()),
        sa.column("text", sa.Text()),
        sa.column("tool_uses", sa.JSON()),
        sa.column("message", sa.JSON()),
    )
    rows = bind.execute(sa.select(jobs)).mappings()
    for row in rows:
        bind.execute(
            jobs.update()
            .where(jobs.c.id == row["id"])
            .values(
                messages=_messages(row["messages"]),
                tools=_tools(row["tools"]),
                message=_result_message(row["text"], row["tool_uses"]),
            )
        )

    columns = {column["name"] for column in inspect(bind).get_columns(_TABLE)}
    with op.batch_alter_table(_TABLE) as batch:
        for column_name in ("contact_id", "text", "thinking", "tool_uses", "raw_blocks"):
            if column_name in columns:
                batch.drop_column(column_name)


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
