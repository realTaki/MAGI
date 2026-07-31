"""Make MAGI membership a single direct MAGIS home.

Revision ID: 0003_single_direct_magis_membership
Revises: 0002_magis_membership_instructions
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_single_direct_magis_membership"
down_revision = "0002_magis_membership_instructions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    unique_names = {
        constraint.get("name")
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints("magis_memberships")
    }
    # The consolidated baseline already has the final constraint for fresh
    # databases; only databases upgraded through 0002 need the conversion.
    if "uq_magis_memberships_magic" in unique_names:
        return
    # Earlier development builds briefly allowed up to three memberships.
    # Keep the oldest direct assignment deterministically, then enforce the
    # new model at the database boundary.
    op.execute(
        "DELETE FROM magis_memberships WHERE id NOT IN "
        "(SELECT MIN(id) FROM magis_memberships GROUP BY magic_id)"
    )
    with op.batch_alter_table("magis_memberships") as batch:
        if "uq_magis_memberships_magis_magic" in unique_names:
            batch.drop_constraint("uq_magis_memberships_magis_magic", type_="unique")
        batch.create_unique_constraint("uq_magis_memberships_magic", ["magic_id"])


def downgrade() -> None:
    with op.batch_alter_table("magis_memberships") as batch:
        batch.drop_constraint("uq_magis_memberships_magic", type_="unique")
        batch.create_unique_constraint("uq_magis_memberships_magis_magic", ["magis_id", "magic_id"])
