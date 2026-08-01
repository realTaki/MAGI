"""Proactive task-preset policy — materialise per-user task snapshots.

When a contact transitions to ``role='assigned'`` (either via
the initial ``POST /api/contacts`` create or via a
``PATCH /api/contacts/{id}`` that flips the role), the
:func:`seed_presets_for_contact` helper walks the operator's
enabled ``TaskPreset`` rows and ensures a corresponding
``Task`` row exists for the new owner. The result is a small
suite of operator-curated scheduled tasks waiting for the
assigned user without the operator having to author each one
by hand.

Idempotent
----------

The policy checks for an existing row per ``(uid,
preset_id)`` pair BEFORE inserting — repeating the hook
against the same contact (e.g. role assigned → admin →
assigned again) is a no-op, not a duplicate. This is the same
pattern :func:`magi.channels.webui.api.action_items._ensure_llm_credentials_item`
uses for the "set your LLM credentials" action item: SELECT
short-circuit + caller commits.

Snapshot semantics
------------------

The per-user ``Task`` row is built from the preset's fields
verbatim (``prompt``, ``frequency`` / moment fields → 5-field
``cron``, ``target_channel``, …). Editing the preset
template later does NOT rewrite existing per-user rows —
they keep their snapshotted config. New assigned contacts
seeded after the edit pick up the new config; existing ones
don't.

Why bypass ``create_task``
--------------------------

The HTTP ``create_task`` route stamps ``uid`` from the
signed-in cookie via
:meth:`magi.channels.webui.api.tasks._resolve_creator_id`. In
the seed hook, ``uid`` must be the *assigned contact*, not
the admin who triggered the role change. Bypassing the
route avoids the cookie lookup and lets us set ``uid``
directly on the new ``Task`` row.

A ``ChatSession`` row is allocated for the task's "home" —
the per-fire conversation the runner appends to — using the
same shape :class:`magi.channels.webui.api.tasks.create_task`
already does at its allocation step (channel="task",
``delivery_address`` stamped via the dispatcher). The
allocation is inlined here rather than imported from
``create_task`` because that helper is part of the FastAPI
route surface (it pulls a ``Request``, opens a
``Depends(get_session)`` transaction, etc.).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from magi.agent.db import ChatSession, Contact
from magi.agent.memory.session import new_session_id
from magi.channels.tasks.cron_utils import (
    preset_to_cron,
    validate_run_at,
)
from magi.channels.tasks.models import Task
from magi.proactive.models import TaskPreset

logger = logging.getLogger("magi.proactive.task_presets")


def seed_presets_for_contact(session: Session, contact_id: int) -> int:
    """For each enabled ``TaskPreset``, ensure a ``Task`` row
    exists for the contact.

    Returns the number of NEW ``Task`` rows inserted. The
    caller is responsible for ``session.commit()`` — the
    surrounding contact-route handlers have their own commit
    points and we don't want to introduce a second one that
    could collide with the contact-row transaction.

    Idempotent: skips presets that already have a per-user
    row (matching on ``uid + preset_id``). Safe to call from
    both ``create_contact`` (when the initial role is
    ``assigned``) and ``update_contact`` (when role
    transitions TO assigned).

    Skips the entire batch if the contact's role isn't
    ``assigned`` — guards against accidental calls from
    admin→assigned→admin patches (we only seed on the
    *first* transition into assigned; the call site is
    responsible for detecting that boundary).
    """
    contact = session.get(Contact, contact_id)
    if contact is None:
        logger.warning(
            "seed_presets_for_contact: contact %d vanished mid-flight",
            contact_id,
        )
        return 0
    if contact.role != "assigned":
        # Caller should have gated on this; the second-line
        # guard keeps the helper a no-op even if a future
        # caller forgets the role-transition check.
        return 0

    # Iterate presets in stable key order so the seeded
    # task names line up across runs (helpful for the
    # operator eyeballing the "预设任务" list).
    presets = session.scalars(
        select(TaskPreset)
        .where(TaskPreset.enabled == 1)
        .order_by(TaskPreset.key.asc())
    ).all()

    inserted = 0
    for preset in presets:
        # Per-preset existence check. ``preset_id`` is
        # nullable, but we never seed a row with
        # ``preset_id IS NULL`` here — the seed always
        # carries the back-pointer so the UI grouping
        # reads correctly.
        existing = session.scalar(
            select(Task.id).where(
                Task.uid == contact_id,
                Task.preset_id == preset.id,
            ).limit(1)
        )
        if existing is not None:
            continue

        task_row = _build_task_from_preset(preset, contact)
        if task_row is None:
            # Skip-and-log rather than raise: a single bad
            # preset shouldn't block the rest from seeding.
            # The operator sees the missing row in the UI
            # and can inspect logs / fix the template.
            continue

        # Allocate the home ChatSession for this task's
        # fires. Mirrors create_task's allocation: a
        # fresh ULID, channel="task", the assigned
        # user's TG chat id (if any) carried as a
        # breadcrumb for the runner's TG-push wiring.
        task_session_id, task_session = _allocate_home_session(
            contact_id=contact_id,
        )
        if task_session is not None:
            session.add(task_session)
            session.flush()  # so task_row.session_id FK resolves
            task_row.session_id = task_session_id

        session.add(task_row)
        inserted += 1

    if inserted > 0:
        logger.info(
            "seed_presets_for_contact: contact=%d role=assigned "
            "inserted %d preset task(s)",
            contact_id, inserted,
        )
        # The scheduler's _rehydrate_from_db path picks up
        # the new rows on its next cycle, so we don't need
        # to nudge it here. Calling ``get_scheduler()`` from
        # inside a route-handler session is also fragile —
        # the scheduler module pulls in ``apscheduler`` +
        # ``magi.channels.tasks.runner`` and importing those
        # while a SQLite ``BEGIN IMMEDIATE`` is already held
        # has historically tripped the "database is locked"
        # on tests that share a single connection across
        # the FastAPI request and the scheduler helper.
        # The DB row is authoritative; ``_rehydrate_from_db``
        # covers the (rare) case of a long-running process
        # that wouldn't otherwise restart.

    return inserted


def _build_task_from_preset(
    preset: TaskPreset, contact: Contact,
) -> Optional[Task]:
    """Render a preset into a fresh ``Task`` row.

    Returns ``None`` if the preset's scheduling config is
    invalid (logged so the operator can debug). The
    snapshotted fields are:

    - ``name``         — preset.name (operator-facing label)
    - ``prompt``       — preset.prompt verbatim
    - ``cron``         — rendered via :func:`preset_to_cron`
                        (or empty for ``once``)
    - ``run_at``       — preset.run_at verbatim for ``once``
    - ``target_channel`` — preset.target_channel
    - ``uid``          — contact_id (the assigned user)
    - ``enabled``      — preset.enabled (so an operator
                        who disables a template doesn't
                        immediately disable already-seeded
                        rows either way — they keep
                        running)
    - ``preset_id``    — preset.id (back-pointer)
    - ``preset_key``   — preset.key (immutable snapshot)

    The ``enabled`` default is a deliberate semantic
    choice: ``preset.enabled`` is the operator's
    "should new contacts get this preset" knob, NOT a
    global mute for existing rows. A per-user ``Task``
    can still be toggled individually from the WebUI
    after seed.
    """
    now_iso = _now_iso()
    cron = ""
    run_at_iso: Optional[str] = None
    if preset.frequency == "once":
        if not preset.run_at:
            logger.warning(
                "preset %r has frequency=once but no run_at; skipping",
                preset.key,
            )
            return None
        try:
            run_at_iso = validate_run_at(preset.run_at)
        except ValueError as exc:
            logger.warning(
                "preset %r has invalid run_at %r (%s); skipping",
                preset.key, preset.run_at, exc,
            )
            return None
    else:
        try:
            cron = preset_to_cron(
                preset.frequency,
                hour=preset.hour,
                minute=preset.minute,
                day_of_week=preset.day_of_week,
                day_of_month=preset.day_of_month,
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "preset %r has invalid scheduling (%s); skipping",
                preset.key, exc,
            )
            return None

    return Task(
        id=new_session_id(),
        # Disambiguate the per-user name by appending the
        # contact's display label. ``tasks.name`` carries a
        # global UNIQUE constraint (set up for the
        # ``schedule_task`` LLM tool's idempotent-upsert
        # contract), so two assigned users can't both own
        # a row literally named "每日晨报". Appending the
        # contact's label keeps the preset's identity
        # visible at a glance while preventing the UNIQUE
        # collision. The operator-facing cell renders the
        # full string; the badge column carries
        # ``preset_key`` so the source is still
        # machine-readable.
        name=f"{preset.name} ({contact.name})",
        prompt=preset.prompt,
        cron=cron,
        run_at=run_at_iso,
        # Per the ``Task`` model: ``tz`` is a forensic
        # breadcrumb stamped at write-time. The runtime
        # reads ``system.timezone`` on every fire so a
        # later tz change moves the row to the new local
        # schedule without touching this column.
        # ``tz`` is a forensic breadcrumb (audit-trail) — the
        # runtime reads ``system.timezone`` on every fire so a
        # later tz change moves the row to the new local schedule
        # without touching this column. Stamping it at seed time
        # is best-effort; we hardcode UTC here rather than calling
        # ``state_get`` inside the seed transaction (same D.28
        # deadlock pattern that ``create_task`` documents —
        # ``state_get`` opens its own SQLite session, and a
        # nested ``BEGIN IMMEDIATE`` against our already-open
        # outer txn deadlocks). The runtime never reads this
        # column, so a stale-at-seed value is harmless.
        tz="UTC",
        target_channel=preset.target_channel,
        # ``delivery_to`` for channel="tg" would normally
        # be the operator's bound TG chat id, but the seed
        # helper doesn't have the dispatcher handy without
        # pulling in the channel runtime — and a TG
        # task can resolve its own destination at fire
        # time via the runner's existing fallback path.
        # Leaving ``None`` lets the runner fall back to
        # the contact's bound chat id at fire time, which
        # is the correct semantic for "preset for an
        # assigned user". channel="webui" ignores
        # ``delivery_to`` entirely.
        delivery_to=None,
        session_id=None,  # set after the ChatSession row is flushed
        uid=contact.id,
        enabled=int(preset.enabled),
        consecutive_failures=0,
        last_run_at=None,
        last_status=None,
        last_error=None,
        created_at=now_iso,
        updated_at=now_iso,
        preset_id=preset.id,
        preset_key=preset.key,
    )


def _allocate_home_session(
    *, contact_id: int,
) -> tuple[str, Optional[ChatSession]]:
    """Allocate the ``ChatSession`` row a seeded task
    accumulates its fires into.

    Returns ``(session_id, ChatSession_or_None)``.

    ``delivery_address`` is left empty here on purpose. The
    dispatcher's :func:`lookup_im_id` opens its own SQLite
    session internally (same D.28 deadlock concern
    documented on :func:`magi.channels.webui.api.tasks.create_task`);
    calling it inside a route-scoped ``Depends(get_session)``
    transaction — which is exactly the context the contacts
    hook fires from — would deadlock on ``BEGIN IMMEDIATE``.
    Instead the runner resolves the contact's bound TG chat
    id at fire time via the dispatcher; an empty
    ``delivery_address`` is the documented sentinel that
    triggers that fallback.

    The session's channel is always ``"task"`` — the runner
    branches on ``Task.target_channel`` for delivery, and
    the session itself never needs to route through TG.
    """
    session_id = new_session_id()
    chat_session = ChatSession(
        session_id=session_id,
        # Empty on purpose — see the docstring above. The
        # runner's fire-time lookup resolves the actual
        # bound chat id from the contact row.
        delivery_address="",
        uid=contact_id,
        channel="task",
        # The chat-history pane reads this title; the
        # "[定时]" prefix matches what ``create_task``
        # writes so the per-user preset tasks render the
        # same way as operator-authored ones in the
        # session list.
        title="[定时] 预设任务",
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    return session_id, chat_session


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
