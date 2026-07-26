"""Adopt the pre-Alembic MAGI schema.

This first revision is deliberately non-destructive:

* a fresh database gets every table represented by the current ORM metadata;
* an existing C0/C1 database keeps its rows and only receives missing tables;
* the runtime performs the one-time legacy inline compatibility pass before
  this revision when it detects a database without ``alembic_version``.

All schema changes after this revision must be separate, reviewed Alembic
revisions. Do not add new ``ALTER TABLE`` statements to the legacy runner.
"""

from __future__ import annotations

from alembic import op

# Import all models so this frozen baseline can create a fresh database from
# the metadata that existed when revision 0001 was introduced.
import magi.agent.db.models_action_item  # noqa: F401,E402
import magi.agent.db.models_contact  # noqa: F401,E402
import magi.agent.db.models_magi  # noqa: F401,E402
import magi.agent.db.models_magic  # noqa: F401,E402
import magi.agent.db.models_setting  # noqa: F401,E402
import magi.agent.db.models_token_usage  # noqa: F401,E402
import magi.agent.memory.magi.models  # noqa: F401,E402
import magi.agent.memory.session.tables  # noqa: F401,E402
import magi.agent.proactive.orm_models  # noqa: F401,E402
from magi.agent.db.base import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    # The baseline is an adoption boundary. Dropping the complete application
    # database from a downgrade is too destructive; restore from a backup or
    # write an explicit, reviewed rollback migration instead.
    pass
