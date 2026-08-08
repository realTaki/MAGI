"""Preset task seeding — handle SeedPresetTasksJob via new_bus TaskBook.

Reads preset templates from ``tasks_book``, builds per-user Task rows,
and inserts them with idempotency (``uid + name`` check).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from magi.new_bus.guild.seedPresetTasksJob import SeedPresetTasksResult
from magi.new_bus.library.local.tasksBook import SOURCE_USER
from magi.proactive.task_presets import (
    ContactSnapshot,
    TaskPresetSnapshot,
    plan_presets_for_contact,
)

if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.seedPresetTasksJob import (
        SeedPresetTasksJob,
    )

logger = logging.getLogger("magi.proactive.preset_tasks")


async def handle_seed_job(bus: "NewBus", job: "SeedPresetTasksJob") -> None:
    """Claim + execute a SeedPresetTasksJob using new_bus Books."""
    try:
        contact = bus.contacts_book.get(contact_id=job.contact_id)
        if contact is None:
            _submit_failure(bus, job, f"contact {job.contact_id} not found")
            return

        contact_snapshot = ContactSnapshot(
            id=contact.id,
            name=contact.name,
            display_name=contact.display_name,
            role=contact.role,
        )

        # Read proactive (preset) templates visible to this contact.
        preset_tasks = bus.tasks_book.list_proactive_tasks(uid=job.contact_id)
        preset_snapshots = [
            TaskPresetSnapshot(
                id=str(t.id),
                key=str(t.key or ""),
                name=str(t.name),
                prompt=str(t.prompt),
                cron=t.cron,
                run_at=t.run_at,
                target_channel=str(t.target_channel),
                enabled=t.enabled,
            )
            for t in preset_tasks
        ]

        # Resolve system timezone for the forensic breadcrumb.
        tz = _read_system_timezone(bus)

        plan = plan_presets_for_contact(
            contact_snapshot,
            preset_snapshots,
            system_timezone=tz,
        )

        inserted = 0
        skipped = len(plan.skipped)
        for seed in plan.seeds:
            # Idempotency: Task.name is UNIQUE, so a row with the
            # same name + same uid means it was already seeded.
            existing = bus.tasks_book.get_by_name(name=seed.name)
            if existing is not None and existing.uid == job.contact_id:
                skipped += 1
                continue

            try:
                kwargs: dict = dict(
                    name=seed.name,
                    prompt=seed.prompt,
                    target_channel=seed.target_channel,
                    uid=job.contact_id,
                    tz=seed.tz,
                    delivery_to=seed.delivery_to,
                    source=SOURCE_USER,
                )
                if seed.cron:
                    kwargs["cron"] = seed.cron
                else:
                    kwargs["run_at"] = seed.run_at
                bus.tasks_book.add(**kwargs)
                inserted += 1
            except ValueError as exc:
                logger.warning(
                    "preset_tasks: seed skipped for contact=%d preset=%s: %s",
                    job.contact_id, seed.preset_key, exc,
                )
                skipped += 1

        result = SeedPresetTasksResult(
            job_id=job.job_id,
            success=True,
            inserted=inserted,
            skipped=skipped,
        )
        bus.seed_preset_tasks_job_board.submit_result(
            key=job.job_id, result=result,
        )

    except Exception as exc:
        logger.exception("preset_tasks: seed job %s failed", job.job_id)
        _submit_failure(bus, job, str(exc))


def _submit_failure(
    bus: "NewBus", job: "SeedPresetTasksJob", error: str,
) -> None:
    try:
        result = SeedPresetTasksResult(
            job_id=job.job_id,
            success=False,
            error=error[:8000],
        )
        bus.seed_preset_tasks_job_board.submit_result(
            key=job.job_id, result=result,
        )
    except Exception:
        logger.exception(
            "preset_tasks: failed to submit seed failure for %s",
            job.job_id,
        )


def _read_system_timezone(bus: "NewBus") -> str:
    """Read ``system.timezone`` from settings_book, default UTC."""
    try:
        raw = bus.settings_book.get("system.timezone")
        if raw and isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception:
        pass
    return "UTC"
