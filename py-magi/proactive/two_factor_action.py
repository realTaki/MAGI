"""Reconcile the local high-priority IM two-factor reminder."""

from __future__ import annotations

from old_bus.firmwares.books.local.actionItemBook import (
    ActionItem,
    ActionItemBook,
    ActionPriority,
    ActionSource,
)
from old_bus.firmwares.books.magis import AUTH_MODE_IM_2FA_ENABLED

TITLE = "启用 IM 两步验证"
DESCRIPTION = "绑定 Telegram 或其他 IM 验证通道，使用一次性验证码保护 MAGI 管理员登录。"
TARGET_URL = "/dashboard?tab=settings&section=security"
COMPLETED_VISIBLE_DAYS = 7


def reconcile_for_admin(
    *, book: ActionItemBook, contact_id: int, auth_mode: str
) -> bool:
    """Keep exactly one open reminder while this admin lacks IM 2FA.

    The item remains Contact-owned because ActionItem is local data.  A
    completed/dismissed historical item never suppresses a required reminder.
    Returns whether a row was inserted or closed.
    """
    rows = [
        item
        for item in book.list_actions(
            owner_contact_id=contact_id,
            include_completed=True,
            completed_visible_days=COMPLETED_VISIBLE_DAYS,
            source=ActionSource.PROACTIVE,
        )
        if item.title == TITLE
    ]
    open_rows = [item for item in rows if item.completed_at is None]
    if auth_mode == AUTH_MODE_IM_2FA_ENABLED:
        changed = False
        for item in open_rows:
            book.complete(
                action_item_id=item.id,
                note="IM two-factor verification enabled",
            )
            changed = True
        return changed
    if open_rows:
        return False
    book.add(ActionItem(
        contact_id=contact_id,
        title=TITLE,
        description=DESCRIPTION,
        target_url=TARGET_URL,
        priority=ActionPriority.HIGH,
        source=ActionSource.PROACTIVE,
    ))
    return True


__all__ = ["DESCRIPTION", "TARGET_URL", "TITLE", "reconcile_for_admin"]
