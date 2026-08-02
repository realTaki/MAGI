"""Add the auth_credentials table for password login.

Revision ID: 0006_auth_credentials
Revises: 0005_agent_bus
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_auth_credentials"
down_revision = "0005_agent_bus"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())

    if "auth_credentials" not in existing:
        op.create_table(
            "auth_credentials",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("uid", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("secret_hash", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["uid"], ["contacts.id"],
                name="fk_auth_credentials_uid",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "uid", "kind", name="ux_auth_credentials_uid_kind",
            ),
        )
        op.create_index(
            "ix_auth_credentials_uid",
            "auth_credentials",
            ["uid"],
        )


def downgrade() -> None:
    op.drop_index("ix_auth_credentials_uid", table_name="auth_credentials")
    op.drop_table("auth_credentials")
