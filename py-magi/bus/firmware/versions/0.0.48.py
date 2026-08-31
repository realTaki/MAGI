"""Remove BUS lifecycle fields from durable LLM tool values.

Revision ID: 0.0.48
Revises: 0.0.47
Create Date: 2026-08-31
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.48"
down_revision = "0.0.47"
branch_labels = None
depends_on = None


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


def _tool_call(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    function = value.get("function") if isinstance(value.get("function"), dict) else value
    tool_call_id = function.get("tool_call_id") or function.get("id")
    name = function.get("name")
    if not tool_call_id or not name:
        return None
    return {
        "tool_call_id": str(tool_call_id),
        "name": str(name),
        "arguments": _arguments(function.get("arguments", function.get("input"))),
    }


def _message(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    cleaned = dict(value)
    if isinstance(value.get("tool_calls"), list):
        cleaned["tool_calls"] = [
            call for item in value["tool_calls"] if (call := _tool_call(item)) is not None
        ]
    return cleaned


def _messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [message for item in value if (message := _message(item)) is not None]


def _tool(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    function = value.get("function") if isinstance(value.get("function"), dict) else value
    name = function.get("name")
    if not name:
        return None
    return {
        "name": str(name),
        "description": str(function.get("description") or ""),
        "input_schema": function.get("input_schema") or function.get("parameters") or {},
    }


def _tools(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [tool for item in value if (tool := _tool(item)) is not None]


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "jobs_call_llm"
    if table_name not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    required = {"id", "messages", "tools", "message"}
    if not required <= columns:
        return

    jobs = sa.table(
        table_name,
        sa.column("id", sa.Integer()),
        sa.column("messages", sa.JSON()),
        sa.column("tools", sa.JSON()),
        sa.column("message", sa.JSON()),
    )
    for row in bind.execute(sa.select(jobs)).mappings():
        bind.execute(
            jobs.update()
            .where(jobs.c.id == row["id"])
            .values(
                messages=_messages(row["messages"]),
                tools=_tools(row["tools"]),
                message=_message(row["message"]),
            )
        )


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
