"""Make ``magic.name`` unique (allow NULL; non-null values must be unique).

Drives the new MAGI creation flow: the WebUI create form no longer accepts
a free-form name — it pre-fills ``EVA-{max_id+1}`` and lets the operator
override, but uniqueness is enforced so two MAGIs cannot collide on the
display string.

The pre-migration check looks for duplicate non-null names and renames
duplicates to ``<original>-<id>`` so the unique index can be built
without losing data.  In practice the seed always picks unique names
(``EVA-000`` for bootstrap, ``EVA-001`` … for subsequent creates) so
this is a safety net rather than the common path.

Revision ID: 0005_magic_name_unique
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005_magic_name_unique"
down_revision = "0004_hook_plugin_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("magic"):
        # No magic table — nothing to do. (This migration runs on
        # private SQLite only; the public PG schema is created via
        # ``Base.metadata.create_all`` which already declares the
        # uniqueness via the SQLAlchemy model.)
        return

    # De-duplicate any existing rows whose ``name`` collides. The
    # bootstrap seed and the new auto-naming default avoid collisions
    # in practice, but pre-existing rows (operator-typed duplicates,
    # test fixtures) need rescuing before the index builds.
    dupes = bind.execute(
        sa.text(
            "SELECT name, COUNT(*) AS n FROM magic "
            "WHERE name IS NOT NULL "
            "GROUP BY name HAVING COUNT(*) > 1"
        )
    ).all()
    for name, _count in dupes:
        rows = bind.execute(
            sa.text("SELECT id FROM magic WHERE name = :n ORDER BY id"),
            {"n": name},
        ).all()
        # Keep the lowest id with the original name; suffix the rest.
        for row in rows[1:]:
            new_name = f"{name}-{row[0]}"
            bind.execute(
                sa.text("UPDATE magic SET name = :new WHERE id = :id"),
                {"new": new_name, "id": row[0]},
            )

    op.create_index(
        "uq_magic_name",
        "magic",
        ["name"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("magic"):
        return
    op.drop_index("uq_magic_name", table_name="magic")
