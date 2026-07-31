"""Separate MAGI identity from MAGIS membership and add instructions.

Revision ID: 0002_magis_membership_instructions
Revises: 0001_baseline
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_magis_membership_instructions"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fresh databases are created from the updated baseline, which already
    # contains this final shape. Existing 0001 databases need the transform
    # below. This revision deliberately supports both paths.
    inspector = sa.inspect(op.get_bind())
    if "instruction" in {column["name"] for column in inspector.get_columns("magis")}:
        return

    # Preserve every existing assignment as a membership before the legacy
    # one-MAGIS / one-position columns are removed.
    with op.batch_alter_table("magis") as batch:
        batch.add_column(sa.Column("instruction", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("magic") as batch:
        batch.add_column(sa.Column("instruction", sa.Text(), nullable=False, server_default=""))

    op.create_table(
        "magis_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("magis_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_reserved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["magis_id"], ["magis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("magis_id", "name", name="uq_magis_roles_magis_name"),
    )
    op.create_index("ix_magis_roles_magis_id", "magis_roles", ["magis_id"])
    op.create_table(
        "magis_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("magis_id", sa.Integer(), nullable=False),
        sa.Column("magic_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["magis_id"], ["magis.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["magic_id"], ["magic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["magis_roles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("magis_id", "magic_id", name="uq_magis_memberships_magis_magic"),
    )
    op.create_index("ix_magis_memberships_magis_id", "magis_memberships", ["magis_id"])
    op.create_index("ix_magis_memberships_magic_id", "magis_memberships", ["magic_id"])
    now = "datetime('now')"
    op.execute(
        "INSERT INTO magis_roles (magis_id, name, instruction, is_reserved, created_at, updated_at) "
        f"SELECT id, 'Adam', 'You are the team leader for this MAGIS. Coordinate work, clarify goals, and surface conflicts or risks to the administrator.', 1, {now}, {now} FROM magis"
    )
    op.execute(
        "INSERT INTO magis_roles (magis_id, name, instruction, is_reserved, created_at, updated_at) "
        f"SELECT id, 'EVE', 'You are a general-purpose member of this MAGIS. Collaborate with the team, carry out assigned work carefully, and report blockers clearly.', 1, {now}, {now} FROM magis"
    )
    op.execute(
        "INSERT INTO magis_memberships (magis_id, magic_id, role_id, created_at, updated_at) "
        "SELECT m.magis_id, m.id, r.id, m.created_at, m.updated_at FROM magic m "
        "JOIN magis_roles r ON r.magis_id = m.magis_id "
        "AND r.name = CASE WHEN lower(m.magic_position) = 'adam' THEN 'Adam' ELSE 'EVE' END"
    )

    with op.batch_alter_table("magic") as batch:
        batch.drop_column("magis_id")
        batch.drop_column("magic_position")

    with op.batch_alter_table("eve_runtimes") as batch:
        batch.drop_index("ix_eve_runtimes_magi_id")
        batch.alter_column("magi_id", new_column_name="magic_id")
        batch.create_index("ix_eve_runtimes_magic_id", ["magic_id"], unique=True)


def downgrade() -> None:
    raise RuntimeError("Downgrading MAGIS memberships is not supported")
