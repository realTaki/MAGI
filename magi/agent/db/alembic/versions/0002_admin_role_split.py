"""Split ``Contact.role`` and add a separate ``admin`` boolean.

The pre-0002 schema conflated two orthogonal concepts on
``Contact.role``:

  1. MAGI's relationship to this person (``assigned`` /
     ``guest``).
  2. WebUI sign-in rights (``role='admin'`` meant "can
     sign into the operator console").

This made it impossible to express "this is the served
user AND also an operator" — one column, one value, one
concept. Operators who are themselves the served user had
to drop one of the two.

After this revision:

  - ``role`` keeps the relationship semantic. The valid
    set shrinks from ``{admin, assigned, contact, guest}``
    to ``{assigned, guest}`` — ``'admin'`` and
    ``'contact'`` are no longer reachable from this
    column. ``'contact'`` was always functionally identical
    to ``'guest'`` (the gate logic refused both); the
    split collapses the two.
  - New ``admin`` boolean column (NOT NULL, default 0)
    carries WebUI sign-in rights. Independent of ``role``.

A contact can now be:

  - ``role=assigned, admin=False`` — pure served user.
  - ``role=assigned, admin=True``  — served user who is
    also an operator (the typical single-MAGI install).
  - ``role=guest,   admin=False``  — external (default
    for ``POST /api/contacts`` with no role specified).
  - ``role=guest,   admin=True``   — never happens in
    practice (``guest`` is reserved for strangers), but
    the schema doesn't reject the combo.

Data migration semantics
-------------------------

Existing rows where ``role='admin'`` are interpreted as
"this person was the operator AND was being served by
this MAGI" (the dev/test setup pattern; production rows
are the same — admins ARE the served user in a single-
operator install). They become
``role='assigned', admin=True``.

Existing rows where ``role='contact'`` collapse to
``role='guest'``. The role was never used as a distinct
gate — every place that refused ``contact`` also refused
``guest``, so collapsing is lossless for runtime behavior.
The ``admin`` bit stays as it was (most pre-0002
``contact`` rows had ``admin=False``; the operator
``admin=True, role='contact'`` shape was uncommon).

Rows where ``role`` was already ``'assigned'`` or ``'guest'``
keep their value; ``admin=False`` applies (the column
default) because they weren't admins under the old schema
either.

Downgrade
---------

Best-effort. Any ``admin=True AND role='assigned'`` row
goes back to ``role='admin'``; pure ``role='assigned'``
served users (``admin=False``) keep ``role='assigned'``
because there's no clean inverse — the operator has to
pick a different value manually if they want to roll
back. ``role='guest'`` is left as-is on rollback; the
old enum value ``'contact'`` is not restored because no
real distinction existed in the first place.
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

    # 3. Collapse the ``role='contact'`` row to
    #    ``role='guest'``. The two values were never
    #    treated distinctly at the gate layer — every
    #    tool / API branch that refused ``contact`` also
    #    refused ``guest``. Collapsing is lossless at
    #    runtime; the row's ``admin`` bit stays as it
    #    was (the contact role's only real distinction
    #    from guest was the row's source semantics, not
    #    the gate).
    op.execute(
        sa.text(
            "UPDATE contacts SET role='guest' "
            "WHERE role='contact'"
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