
"""Persistent desired/observed state for an EVA runtime.

``Magi`` models the organisational identity of an agent.  This module models
the separately managed execution resource for an EVA: a Kubernetes
Deployment and private workspace PVC. Provider configuration is resolved from
the direct MAGIS database. Keeping these concerns in a dedicated one-to-one
table means an EVA can be configured while
it is stopped without treating a missing Pod as a missing Magi.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive

EVE_DESIRED_STATES = frozenset({"draft", "running", "stopped", "deleted"})
EVE_OBSERVED_STATES = frozenset(
    {"draft", "provisioning", "running", "stopped", "failed", "deleting", "deleted"}
)


class EveRuntime(Base):
    """Desired and observed runtime state for one EVA ``Magi`` row.

    Resource names are persisted after the orchestrator accepts a request so
    retries remain idempotent and an operator can diagnose orphaned resources.
    The controller, not the ADAM container, owns the actual Kubernetes API
    calls.
    """

    __tablename__ = "eve_runtimes"

    id: Mapped[int] = mapped_column(primary_key=True)
    magic_id: Mapped[int] = mapped_column(
        ForeignKey("magic.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    desired_state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    observed_state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    namespace: Mapped[str | None] = mapped_column(String(63), nullable=True)
    deployment_name: Mapped[str | None] = mapped_column(String(63), nullable=True)
    workspace_claim_name: Mapped[str | None] = mapped_column(String(63), nullable=True)
    credential_secret_name: Mapped[str | None] = mapped_column(String(63), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )
