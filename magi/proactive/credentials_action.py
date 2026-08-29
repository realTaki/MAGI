"""Credentials nudge: spec + idempotent insert.

The single source of truth for the "set your LLM provider + API key"
action item every admin sees.  It is reconciled at worker start-up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.old_bus.firmwares.books.local.actionItemBook import ActionItemBook

from magi.old_bus.firmwares.books.local.actionItemBook import ActionItem, ActionSource

logger = logging.getLogger("magi.proactive.credentials_action")
COMPLETED_VISIBLE_DAYS = 7


@dataclass(frozen=True, slots=True)
class CredentialsNudgeSpec:
    """Static content for the credentials nudge.

    Frozen so the wizard, the dashboard renderer, and
    tests can introspect the spec without surprise
    mutations.  The stable ``title`` field is the
    idempotency key — callers use it to skip already-open
    / already-completed rows.
    """

    title: str
    description: str
    target_url: str


# The one and only nudge. Stable ``title`` so the
# idempotency check (and any future partial unique
# index) match by exact string — callers and tests
# shouldn't need to know the rest of the content.
CREDENTIALS_NUDGE = CredentialsNudgeSpec(
    title="设置你的 LLM provider 和 API key",
    description=("切到「Contacts」,找到自己的档案,把 Provider 和 API Key 填上。"),
    target_url="/dashboard?tab=organization",
)


def ensure_for_admin(
    *,
    book: ActionItemBook,
    admin_id: int,
) -> bool:
    """Idempotently insert the credentials nudge for one admin.

    Returns ``True`` if a new row was created, ``False`` if
    an open or already-completed nudge exists.
    """
    spec = CREDENTIALS_NUDGE
    existing = [
        row
        for row in book.list_actions(
            owner_contact_id=admin_id,
            include_completed=False,
            source=ActionSource.PROACTIVE,
        )
        if row.title == spec.title
    ]
    if existing:
        logger.debug(
            "credentials_nudge: open nudge already exists for admin=%s; skipping",
            admin_id,
        )
        return False
    # 额外检查：是否已完成
    completed = [
        row
        for row in book.list_actions(
            owner_contact_id=admin_id,
            include_completed=True,
            completed_visible_days=COMPLETED_VISIBLE_DAYS,
            source=ActionSource.PROACTIVE,
        )
        if row.title == spec.title and row.completed_at is not None
    ]
    if completed:
        return False
    book.add(ActionItem(
        contact_id=admin_id,
        title=spec.title,
        description=spec.description,
        target_url=spec.target_url,
        source=ActionSource.PROACTIVE,
    ))
    logger.info(
        "credentials_nudge: inserted for admin=%s (title=%r)",
        admin_id,
        spec.title,
    )
    return True


__all__ = [
    "CredentialsNudgeSpec",
    "CREDENTIALS_NUDGE",
    "ensure_for_admin",
]
