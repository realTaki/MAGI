
"""Add direct MAGIS administrator grants.

Revision ID: 0004_magis_admins
Revises: 0003_single_direct_magis_membership
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_magis_admins"
down_revision = "0003_single_direct_magis_membership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "magis_admins" in inspector.get_table_names():
        return
    op.create_table(
        "magis_admins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("magis_id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["magis_id"], ["magis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("magis_id", "telegram_id", name="uq_magis_admins_magis_telegram"),
    )
    op.create_index("ix_magis_admins_magis_id", "magis_admins", ["magis_id"])
    op.create_index("ix_magis_admins_telegram_id", "magis_admins", ["telegram_id"])


def downgrade() -> None:
    op.drop_index("ix_magis_admins_telegram_id", table_name="magis_admins")
    op.drop_index("ix_magis_admins_magis_id", table_name="magis_admins")
    op.drop_table("magis_admins")
