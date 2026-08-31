"""Reuse Tool and RunToolJob in the durable LLM contract.

Revision ID: 0.0.47
Revises: 0.0.46
Create Date: 2026-08-31
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.47"
down_revision = "0.0.46"
branch_labels = None
depends_on = None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _run_tool_call(value: Any, *, publisher: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    tool_call_id = _text(value.get("id"))
    name = _text(value.get("name"))
    if not tool_call_id or not name:
        return None
    return {
        "publisher": publisher,
        "name": name,
        "tool_call_id": tool_call_id,
        "arguments": _arguments(value.get("input")),
    }


def _messages(value: Any, *, publisher: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        blocks = item.get("content_blocks")
        if role == "assistant":
            calls = [
                call
                for block in blocks or ()
                if isinstance(block, dict) and block.get("type") == "tool_use"
                if (call := _run_tool_call(
                    {"id": block.get("id"), "name": block.get("name"), "input": block.get("input")},
                    publisher=publisher,
                )) is not None
            ]
            block_text = "\n".join(
                _text(block.get("text"))
                for block in blocks or ()
                if isinstance(block, dict) and block.get("type") == "text"
            )
            out.append(
                {
                    "role": "assistant",
                    "text": _text(item.get("content")) or block_text,
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
                if tool_call_id:
                    out.append(
                        {
                            "role": "tool",
                            "text": _text(block.get("content")),
                            "tool_calls": None,
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
                    "tool_calls": None,
                    "tool_call_id": None,
                    "is_error": False,
                }
            )
    return out


def _tools(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else item
        name = _text(function.get("name"))
        if name:
            out.append(
                {
                    "name": name,
                    "description": _text(function.get("description")),
                    "input_schema": function.get("parameters") or function.get("input_schema") or {},
                    "enabled": True,
                }
            )
    return out


def _result_message(text: Any, tool_uses: Any, *, publisher: str) -> dict[str, Any] | None:
    calls = [
        call
        for item in tool_uses or ()
        if (call := _run_tool_call(item, publisher=publisher)) is not None
    ]
    if text is None and not calls:
        return None
    return {
        "role": "assistant",
        "text": _text(text),
        "tool_calls": calls or None,
        "tool_call_id": None,
        "is_error": False,
    }


def _upgrade_call_llm() -> None:
    bind = op.get_bind()
    table_name = "jobs_call_llm"
    if table_name not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    if "max_tokens" not in columns:
        return

    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column("message", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("usage", sa.JSON(), nullable=True))
        batch.alter_column("max_tokens", new_column_name="max_output_tokens", existing_type=sa.Integer())

    jobs = sa.table(
        table_name,
        sa.column("id", sa.Integer()),
        sa.column("publisher", sa.Text()),
        sa.column("messages", sa.JSON()),
        sa.column("tools", sa.JSON()),
        sa.column("text", sa.Text()),
        sa.column("tool_uses", sa.JSON()),
        sa.column("message", sa.JSON()),
    )
    for row in bind.execute(sa.select(jobs)).mappings():
        publisher = _text(row["publisher"])
        bind.execute(
            jobs.update()
            .where(jobs.c.id == row["id"])
            .values(
                messages=_messages(row["messages"], publisher=publisher),
                tools=_tools(row["tools"]),
                message=_result_message(row["text"], row["tool_uses"], publisher=publisher),
            )
        )

    with op.batch_alter_table(table_name) as batch:
        for column_name in ("contact_id", "text", "thinking", "tool_uses", "raw_blocks"):
            batch.drop_column(column_name)


def _rename_run_tool_name() -> None:
    bind = op.get_bind()
    table_name = "jobs_run_tool"
    if table_name not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    if "tool_name" not in columns or "name" in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.alter_column("tool_name", new_column_name="name", existing_type=sa.Text())


def upgrade() -> None:
    _upgrade_call_llm()
    _rename_run_tool_name()


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
