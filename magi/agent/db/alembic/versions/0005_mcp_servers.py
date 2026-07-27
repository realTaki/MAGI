"""Add ``mcp_servers`` table — operator-configured MCP servers.

The :class:`McpServer` row is the canonical source of
truth for which Model-Context-Protocol servers the agent
loop should connect to. The loader
(:mod:`magi.agent.tools.mcp_loader`) reads these rows on
demand; the WebUI Settings → MCP card writes them.

Replaces the old ``mcp.json`` + ``MAGI_MCP_CONFIG`` flow,
which is no longer reachable from production code paths
(migration of existing JSON config is left to the operator
— they re-create the rows via the Settings UI).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_mcp_servers"
down_revision = "0004_action_items_due_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: if ``Base.metadata.create_all`` already built
    # the table (fresh DB), skip.  The table may also exist from
    # a previous migration run.
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "mcp_servers" in insp.get_table_names():
        return
    op.create_table(
        "mcp_servers",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("connection_type", sa.String(length=16), nullable=False),
        sa.Column("command", sa.String(length=256), nullable=True),
        sa.Column("args_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("env_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("headers_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("connect_timeout", sa.Float(), nullable=True),
        sa.Column("execute_timeout", sa.Float(), nullable=True),
        sa.Column("sse_read_timeout", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("mcp_servers")
