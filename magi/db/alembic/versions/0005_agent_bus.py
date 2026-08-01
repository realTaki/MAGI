"""Add the private durable message-bus tables.

Revision ID: 0005_agent_bus
Revises: 0004_magis_admins
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_agent_bus"
down_revision = "0004_magis_admins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    json = sa.JSON()

    if "agent_runs" not in existing:
        op.create_table(
            "agent_runs",
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("root_event_id", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("continuation", json, nullable=True),
            sa.Column("result", json, nullable=True),
            sa.Column("error_code", sa.String(length=96), nullable=True),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("run_id"),
            sa.UniqueConstraint("root_event_id"),
        )
        op.create_index("ix_agent_runs_status_created", "agent_runs", ["status", "created_at"])

    if "agent_inbox" not in existing:
        op.create_table(
            "agent_inbox",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=64), nullable=False),
            sa.Column("source_id", sa.String(length=128), nullable=True),
            sa.Column("payload", json, nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("available_at", sa.DateTime(), nullable=False),
            sa.Column("leased_by", sa.String(length=128), nullable=True),
            sa.Column("leased_until", sa.DateTime(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id"),
        )
        op.create_index("ix_agent_inbox_claim", "agent_inbox", ["status", "available_at", "id"])
        op.create_index("ix_agent_inbox_lease", "agent_inbox", ["status", "leased_until"])
        op.create_index("ix_agent_inbox_run", "agent_inbox", ["run_id", "id"])

    if "run_inputs" not in existing:
        op.create_table(
            "run_inputs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("event_id", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=64), nullable=False),
            sa.Column("payload", json, nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id"),
        )
        op.create_index("ix_run_inputs_run_created", "run_inputs", ["run_id", "created_at", "id"])

    for table, id_column, extra_columns in (
        ("tool_jobs", "job_id", [
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("tool_call_id", sa.String(length=128), nullable=False),
            sa.Column("tool_name", sa.String(length=128), nullable=False),
        ]),
        ("delivery_outbox", "delivery_id", [
            sa.Column("run_id", sa.String(length=64), nullable=True),
            sa.Column("channel", sa.String(length=32), nullable=False),
            sa.Column("destination", sa.String(length=256), nullable=True),
        ]),
    ):
        if table in existing:
            continue
        constraints = [sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint(id_column)]
        if table == "tool_jobs":
            constraints.append(sa.UniqueConstraint("tool_call_id"))
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column(id_column, sa.String(length=64), nullable=False),
            *extra_columns,
            sa.Column("payload", json, nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("available_at", sa.DateTime(), nullable=False),
            sa.Column("leased_by", sa.String(length=128), nullable=True),
            sa.Column("leased_until", sa.DateTime(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            *constraints,
        )
        op.create_index(f"ix_{table}_claim", table, ["status", "available_at", "id"])

    for table, id_column, payload_name in (
        ("tool_calls", "tool_call_id", "arguments"),
        ("a2a_invocations", "invocation_id", "request"),
    ):
        if table in existing:
            continue
        extra = (
            [sa.Column("tool_name", sa.String(length=128), nullable=False)]
            if table == "tool_calls"
            else [sa.Column("target", sa.String(length=512), nullable=False)]
        )
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column(id_column, sa.String(length=128), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            *extra,
            sa.Column(payload_name, json, nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("result", json, nullable=True),
            sa.Column("error", json, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(id_column),
        )
        op.create_index(f"ix_{table}_run_created", table, ["run_id", "created_at"])

    if "llm_attempts" not in existing:
        op.create_table(
            "llm_attempts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("attempt_id", sa.String(length=128), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("phase", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("request", json, nullable=True),
            sa.Column("response", json, nullable=True),
            sa.Column("error", json, nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("attempt_id"),
        )
        op.create_index("ix_llm_attempts_run_started", "llm_attempts", ["run_id", "started_at"])


def downgrade() -> None:
    for table in (
        "llm_attempts", "a2a_invocations", "tool_calls", "delivery_outbox",
        "tool_jobs", "run_inputs", "agent_inbox", "agent_runs",
    ):
        op.drop_table(table)
