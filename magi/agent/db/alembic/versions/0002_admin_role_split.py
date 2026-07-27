"""Split ``Contact.role`` and add a separate ``admin`` boolean.

The pre-0002 schema conflated two orthogonal concepts on
``Contact.role``:

  1. MAGI's relationship to this person (``assigned`` /
     ``contact`` / ``guest``).
  2. WebUI sign-in rights (``role='admin'`` meant "can
     sign into the operator console").

This made it impossible to express "this is the served
user AND also an operator" — one column, one value, one
concept. Operators who are themselves the served user had
to drop one of the two.

After this revision:

  - ``role`` keeps the relationship semantic. The valid
    set shrinks from ``{admin, assigned, contact, guest}``
    to ``{assigned, contact, guest}`` — ``'admin'`` is no
    longer reachable from this column.
  - New ``admin`` boolean column (NOT NULL, default 0)
    carries WebUI sign-in rights. Independent of ``role``.

A contact can now be any combination:

  - ``role=assigned, admin=False`` — pure served user.
  - ``role=assigned, admin=True``  — served user who is
    also an operator.
  - ``role=contact, admin=True``   — colleague with
    backend access (typical operator).
  - ``role=contact, admin=False``  — colleague without
    backend access.
  - ``role=guest,   admin=False``  — external.

Data migration semantics
-------------------------

Existing rows where ``role='admin'`` are interpreted as
"this person was the operator AND was being served by
this MAGI" (the dev/test setup pattern; production rows
are the same — admins ARE the served user in a single-
operator install). They become
``role='assigned', admin=True``.

Rows where ``role`` was already ``'assigned'``, ``'contact'``,
or ``'guest'`` get ``admin=False`` (the migration's column
default applies because they weren't admins under the old
schema either).

Downgrade
---------

Best-effort. Any ``admin=True AND role='assigned'`` row
goes back to ``role='admin'``; pure ``role='assigned'``
served users (``admin=False``) keep ``role='assigned'``
because there's no clean inverse — the operator has to
pick a different value manually if they want to roll
back.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_admin_role_split"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. Add the new column. ``server_default='0'`` so
    #    existing rows (which have no admin concept) land
    #    at ``admin=False`` — they're not operators under
    #    either the old or new schema.
    contacts_cols = {c["name"] for c in insp.get_columns("contacts")}
    if "admin" not in contacts_cols:
        op.add_column(
            "contacts",
            sa.Column(
                "admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )

    # 2. Migrate every pre-existing operator row to the
    #    new shape. ``role='admin'`` is no longer reachable
    #    from the role enum (see the contacts API after
    #    this revision); we lift each one to
    #    ``role='assigned', admin=True`` because in the
    #    pre-split design ``role='admin'`` already implied
    #    "this person is the served user AND has WebUI
    #    access".
    op.execute(
        sa.text(
            "UPDATE contacts SET role='assigned', admin=1 "
            "WHERE role='admin'"
        )
    )


def downgrade() -> None:
    # Best-effort inverse: pull every
    # ``admin=True AND role='assigned'`` row back to
    # ``role='admin'``. Rows where role was already
    # ``'assigned'`` with ``admin=False`` keep
    # ``role='assigned'`` — there's no clean inverse
    # mapping for them, so the operator has to decide
    # manually if they need a clean rollback.
    op.execute(
        sa.text(
            "UPDATE contacts SET role='admin' "
            "WHERE admin=1 AND role='assigned'"
        )
    )
    op.drop_column("contacts", "admin")