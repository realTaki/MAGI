"""Store catalog definitions as one LLM tool value.

Revision ID: 0.0.50
Revises: 0.0.49
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0.0.50"
down_revision = "0.0.49"
branch_labels = None
depends_on = None


def _definition(row: Any) -> dict[str, Any]:
    return {
        "name": str(row["name"] or ""),
        "description": str(row["description"] or ""),
        "input_schema": row["input_schema"] if isinstance(row["input_schema"], dict) else {},
    }


def _upgrade_columns(table_name: str, *, keep_name: bool = False) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "definition" in columns:
        return
    required = {"id", "name", "description", "input_schema"}
    if not required <= columns:
        return

    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column("definition", sa.JSON(), nullable=True))

    table = sa.table(
        table_name,
        sa.column("id", sa.Integer()),
        sa.column("name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("input_schema", sa.JSON()),
        sa.column("definition", sa.JSON()),
    )
    for row in bind.execute(sa.select(table)).mappings():
        bind.execute(table.update().where(table.c.id == row["id"]).values(definition=_definition(row)))

    with op.batch_alter_table(table_name) as batch:
        batch.alter_column("definition", nullable=False, existing_type=sa.JSON())
        if not keep_name:
            batch.drop_column("name")
        batch.drop_column("description")
        batch.drop_column("input_schema")


def _tool(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("definition"), dict):
        return value
    name = value.get("name")
    if not name:
        return None
    cleaned = dict(value)
    cleaned["definition"] = {
        "name": str(name),
        "description": str(value.get("description") or ""),
        "input_schema": value.get("input_schema") if isinstance(value.get("input_schema"), dict) else {},
    }
    return cleaned


def _upgrade_result(table_name: str, column_name: str, *, many: bool) -> None:
    bind = op.get_bind()
    if table_name not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns(table_name)}
    if not {"id", column_name} <= columns:
        return
    table = sa.table(
        table_name,
        sa.column("id", sa.Integer()),
        sa.column(column_name, sa.JSON()),
    )
    for row in bind.execute(sa.select(table)).mappings():
        value = row[column_name]
        cleaned = (
            [tool for item in value or () if (tool := _tool(item)) is not None]
            if many
            else _tool(value)
        )
        if cleaned != value:
            bind.execute(table.update().where(table.c.id == row["id"]).values({column_name: cleaned}))


def upgrade() -> None:
    _upgrade_columns("books_tools", keep_name=True)
    _upgrade_columns("jobs_set_tool")
    _upgrade_result("jobs_get_tool", "tool", many=False)
    _upgrade_result("jobs_list_tools", "tools", many=True)


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
