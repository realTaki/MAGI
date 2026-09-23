"""Preset task seeding — handle one :class:`SeedPresetTaskJob` per call.

Each :class:`SeedPresetTaskJob` carries a ``preset_key`` and the worker
either inserts the matching Task row, no-ops on idempotent re-seed, or
fails the job with a precise ``error`` describing the broken preset.

The caller dispatches **one job per preset** (see
:meth:`channels.api.contacts._publish_contact_creation` /
:meth:`channels.api.contacts._publish_contact_update`), so each
job's success / failure maps 1:1 to one preset's outcome — no bulk
``inserted=3, skipped=2`` counter that needs post-mortem log-diving to
disambiguate.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import yaml

from old_bus.bases.job import JobStatus
from old_bus.firmwares.jobs.seedPresetTasksJob import SeedPresetTaskResult
from old_bus.firmwares.books.local import Role
from old_bus.firmwares.books.local.tasksBook import (
    TaskSource,
    preset_to_cron,
    validate_run_at,
)

if TYPE_CHECKING:
    from old_bus import Bus
    from old_bus.firmwares.jobs.seedPresetTasksJob import SeedPresetTaskJob

logger = logging.getLogger("proactive.preset_tasks")


async def handle_seed_job(bus: Bus, job: SeedPresetTaskJob, *, worker_id: str) -> None:
    """Claim + execute **one** :class:`SeedPresetTaskJob`.

    The job carries ``contact_id`` + ``preset_key``; the worker looks
    up the matching YAML preset, validates it, and either inserts one
    Task row (idempotent on re-seed) or fails with a precise error
    string the caller can show back to the operator.
    """
    try:
        contact = bus.contacts_book.get(job.contact_id)
        if contact is None:
            _submit_failure(bus, job, worker_id, f"contact {job.contact_id} not found")
            return

        if contact.role != Role.ASSIGNED:
            # Caller dispatches per-preset regardless of role; the role
            # guard lives here so re-enabling a contact to non-ASSIGNED
            # doesn't accidentally seed its tasks.
            _submit_success(bus, job, worker_id)
            return

        preset = _load_preset(bus, job.preset_key)
        if preset is None:
            _submit_failure(bus, job, worker_id, f"preset {job.preset_key!r} not found in prompt_book")
            return

        if not preset.get("enabled", True):
            # ``enabled=false`` is a silent no-op, not a failure —
            # operator explicitly disabled this preset, no action needed.
            _submit_success(bus, job, worker_id)
            return

        contact_label = (contact.display_name or contact.name or f"contact {contact.id}").strip()
        tz = _read_system_timezone(bus)

        cron_val, run_at_val = _resolve_schedule(preset)
        if cron_val is None and run_at_val is None and not _is_no_schedule(preset):
            # ``_resolve_schedule`` already logged the precise error
            # (invalid frequency / cron / run_at); we just relay it as
            # the job's failure.
            _submit_failure(
                bus,
                job,
                worker_id,
                f"preset {job.preset_key!r} schedule is invalid "
                "(see worker log for the underlying parse error)",
            )
            return

        task_name = f"{preset.get('name', '')} ({contact_label})"

        # Idempotent: already exists for the same contact → no-op success.
        existing = bus.tasks_book.get_by_name(name=task_name)
        if existing is not None and existing.contact_id == job.contact_id:
            _submit_success(bus, job, worker_id)
            return

        try:
            kwargs: dict = dict(
                name=task_name,
                prompt=str(preset.get("prompt") or ""),
                target_channel=str(preset.get("channel") or "webui"),
                contact_id=job.contact_id,
                tz=tz,
                source=TaskSource.PROACTIVE,
                enabled=1,
            )
            if cron_val:
                kwargs["cron"] = cron_val
            else:
                kwargs["run_at"] = run_at_val
            from old_bus.firmwares.books.local.tasksBook import Task

            bus.tasks_book.add(Task(**kwargs))
        except ValueError as exc:
            _submit_failure(
                bus,
                job,
                worker_id,
                f"tasks_book.add rejected preset {job.preset_key!r}: {exc}",
            )
            return

        logger.info(
            "preset_tasks: seeded preset=%s contact=%d task_name=%r",
            job.preset_key,
            job.contact_id,
            task_name,
        )
        _submit_success(bus, job, worker_id)

    except Exception as exc:
        logger.exception("preset_tasks: seed job %s failed", job.job_id)
        _submit_failure(bus, job, worker_id, str(exc))


# --- helpers -----------------------------------------------------------------


def _is_no_schedule(preset: dict) -> bool:
    """True when the preset has no schedule at all (valid for manual triggers)."""
    return not (preset.get("frequency") or preset.get("cron") or preset.get("run_at"))


def _resolve_schedule(preset: dict) -> tuple[str | None, datetime | None]:
    """Parse ``preset`` into ``(cron, run_at)``. Returns ``(None, None)`` on invalid.

    Logged at WARNING so the operator sees the broken preset without
    needing to grep job ids. The single-preset granularity makes the
    WARNING pinpoint actionable.
    """
    frequency = str(preset.get("frequency") or "")
    if frequency == "once":
        raw_run_at = preset.get("run_at")
        if not raw_run_at:
            logger.warning(
                "preset_tasks: preset %r has frequency=once but no run_at",
                preset.get("key"),
            )
            return None, None
        try:
            run_at_iso = validate_run_at(raw_run_at)
        except ValueError:
            logger.warning(
                "preset_tasks: preset %r run_at=%r is not a valid ISO 8601",
                preset.get("key"),
                raw_run_at,
            )
            return None, None
        return "", run_at_iso
    if frequency in ("hourly", "daily", "weekly", "monthly"):
        try:
            cron_val = preset_to_cron(
                frequency,
                hour=int(preset.get("hour") or 0),
                minute=int(preset.get("minute") or 0),
                day_of_week=preset.get("day_of_week"),
                day_of_month=preset.get("day_of_month"),
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "preset_tasks: preset %r cron build failed: %s", preset.get("key"), exc
            )
            return None, None
        return cron_val, None
    if not frequency:
        # No frequency declared — defer to ``_is_no_schedule``; return
        # sentinels that the caller interprets as "no schedule yet".
        return None, None
    logger.warning(
        "preset_tasks: preset %r has unknown frequency=%r",
        preset.get("key"),
        frequency,
    )
    return None, None


def _load_preset(bus: Bus, preset_key: str) -> dict | None:
    """Read and decode one ProactiveWorker-owned Markdown preset prompt."""
    try:
        content = bus.prompt_book.get(key=f"proactive/{preset_key}")
    except Exception:
        logger.warning("preset_tasks: failed to read preset %r from prompt_book", preset_key)
        return None
    if content is None:
        return None
    if not content.startswith("---\n"):
        raise ValueError(f"preset {preset_key!r} must begin with YAML front matter")
    try:
        raw_metadata, prompt = content[4:].split("\n---\n", maxsplit=1)
    except ValueError as exc:
        raise ValueError(f"preset {preset_key!r} has unterminated YAML front matter") from exc
    metadata = yaml.safe_load(raw_metadata)
    if not isinstance(metadata, dict) or metadata.get("key") != preset_key:
        raise ValueError(f"preset {preset_key!r} has invalid or mismatched front matter key")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError(f"preset {preset_key!r} has an empty prompt body")
    return {**metadata, "prompt": prompt}


def _read_system_timezone(bus: Bus) -> str:
    try:
        raw = bus.settings_book.get_value(key="system.timezone")
        if raw and isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception:
        pass
    return "UTC"


def _submit_success(
    bus: Bus,
    job: SeedPresetTaskJob,
    worker_id: str,
) -> None:
    try:
        result = SeedPresetTaskResult(
            job_id=job.job_id,
            status=JobStatus.COMPLETED,
        )
        bus.seed_preset_task_job_board.submit_result(
            job_id=job.job_id,
            worker_id=worker_id,
            result=result,
        )
    except Exception:
        # Mirror _submit_failure: a result-submission error must not
        # propagate out of the Worker — the preset row (if any) is
        # already committed, and an expired lease remains available for a
        # later worker to reclaim.
        logger.exception(
            "preset_tasks: failed to submit seed success for %s",
            job.job_id,
        )


def _submit_failure(
    bus: Bus,
    job: SeedPresetTaskJob,
    worker_id: str,
    error: str,
) -> None:
    try:
        result = SeedPresetTaskResult(
            job_id=job.job_id,
            status=JobStatus.FAILED,
            error=error[:8000],
        )
        bus.seed_preset_task_job_board.submit_result(
            job_id=job.job_id,
            worker_id=worker_id,
            result=result,
        )
    except Exception:
        logger.exception(
            "preset_tasks: failed to submit seed failure for %s",
            job.job_id,
        )
