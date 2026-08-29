"""Remove conversation owner and ConvMembersBook.

Revision ID: 0.0.24
Revises: 0.0.23
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0.0.24"
down_revision = "0.0.23"
branch_labels = None
depends_on = None


def _drop_table_if_present(table_name: str) -> None:
    if table_name in inspect(op.get_bind()).get_table_names():
        op.drop_table(table_name)


def _drop_column_if_present(table_name: str, column_name: str) -> None:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.drop_column(column_name)


def upgrade() -> None:
    for table_name in (
        "books_conv_members",
        "jobs_add_conversation_member",
        "jobs_list_conversation_members",
        "jobs_remove_conversation_member",
    ):
        _drop_table_if_present(table_name)
    _drop_column_if_present("books_conversations", "owner_contact_id")
    _drop_column_if_present("jobs_create_conversation", "owner_contact_id")


def downgrade() -> None:
    # Firmware revisions are forward-only during the vNext development phase.
    pass
