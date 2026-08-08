"""Onboarding nudge: "set your LLM provider + API key".

The first system-issued action item every admin sees.
Triggered at the end of the onboarding wizard — by then
each admin's row exists, the operator has just signalled
they're ready to start chatting, and a config-blocker
nudge is at its most actionable.

Idempotent: re-running on the same admin is a no-op when
an open nudge already exists. The check + insert split is
non-atomic (see :func:`ensure_for_admin` docstring for
the trade-off), so a partial unique index would be the
proper fix when concurrency becomes a real concern.

Why this lives here, not in :class:`ActionItemBook`
----------------------------------------------------------------

The Book is pure CRUD — it owns rows, not decisions. The
"after onboarding, every admin gets this nudge until they
configure LLM credentials" rule is **policy**: it chooses
the title, the description, the deep-link target, the
provenance tag, and when to fire. All of that is
proactive; the Book is the storage adapter the policy
writes through.

Mirrors the seam in :mod:`magi.proactive.task_presets`:
the planner returns a plan DTO, the caller executes the
inserts via the bus. Here the plan is one
:class:`CredentialsNudgeSpec` constant and the policy
function is :func:`ensure_for_admin`.

Idempotency key
---------------

The nudge is identified by ``title`` — stable as long as
the spec constant below doesn't change. We don't need a
dedicated kind/discriminator column for one proactive
nudge in v0; the action_items table is provenance-tagged
by ``source``, and within ``source='proactive'`` this
nudge is the only one, so the title match is sufficient.
Future proactive policies that want multiple rows per
admin will need their own discriminator (likely an
additional column or a separate table).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from magi.new_bus.library.local.actionItemBook import (
    SOURCE_PROACTIVE,
    ActionItemBook,
)

logger = logging.getLogger("magi.proactive.credentials_nudge")


@dataclass(frozen=True, slots=True)
class CredentialsNudgeSpec:
    """Static content for the credentials nudge.

    Frozen so the wizard, the dashboard renderer, and
    tests can introspect the spec without surprise
    mutations, and so the policy function can build a
    row from it without round-tripping the database.
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
    description=(
        "切到「Contacts」,找到自己的档案,"
        "把 Provider 和 API Key 填上。"
    ),
    target_url="/dashboard?tab=organization",
)


def ensure_for_admin(
    *,
    book: ActionItemBook,
    admin_id: int,
) -> bool:
    """Idempotently insert the credentials nudge for one admin.

    Returns ``True`` if a new row was created, ``False`` if
    an open nudge already exists for ``admin_id``.

    The check + insert runs as two separate Book calls in
    two separate transactions. That's a small race: if two
    callers race on the same ``admin_id`` (extremely rare
    in practice — the only trigger is the onboarding
    wizard's "OK, I'm done" button, single-press per
    session), both could pass the open-existence check and
    both could call ``add()``, yielding a duplicate nudge
    row. The fix is a partial unique index at the schema
    level; the current code relies on the caller's
    single-writer assumption.

    Caller scope: this is called once per admin at the
    end of onboarding. Returns ``True`` for first
    onboard (per admin), ``False`` for re-onboard after
    ``/restart`` (per admin).
    """
    spec = CREDENTIALS_NUDGE
    # Idempotency check: scan the admin's open proactive
    # rows for a title match. Cost is one DB query per
    # onboarding completion — negligible against the
    # once-per-admin-experience call site.
    existing = [
        row for row in book.list_actions(
            owner_uid=admin_id,
            include_completed=False,
            source=SOURCE_PROACTIVE,
        )
        if row.title == spec.title
    ]
    if existing:
        logger.debug(
            "credentials_nudge: open nudge already exists for admin=%s; skipping",
            admin_id,
        )
        return False
    book.add(
        uid=admin_id,
        title=spec.title,
        description=spec.description,
        target_url=spec.target_url,
        source=SOURCE_PROACTIVE,
    )
    logger.info(
        "credentials_nudge: inserted for admin=%s (title=%r)",
        admin_id, spec.title,
    )
    return True


__all__ = [
    "CredentialsNudgeSpec",
    "CREDENTIALS_NUDGE",
    "ensure_for_admin",
]