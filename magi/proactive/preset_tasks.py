"""Preset task seeding — handle SeedPresetTasksJob.

Reads bundled YAML presets from
:meth:`~magi.bus.library.file.promptBook.PromptBook.task_presets`,
converts each into a Task row with ``source=SOURCE_PROACTIVE``, and
inserts idempotently (skip when a task with the same name + uid
already exists).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from magi.bus.guild.seedPresetTasksJob import SeedPresetTasksResult
from magi.bus.library.local.tasksBook import (
    SOURCE_PROACTIVE,
    preset_to_cron,
    validate_run_at,
)

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.seedPresetTasksJob import SeedPresetTasksJob

logger = logging.getLogger("magi.proactive.preset_tasks")


async def handle_seed_job(bus: "Bus", job: "SeedPresetTasksJob") -> None:
    """Claim + execute a SeedPresetTasksJob.

    从 prompt_book.task_presets() 读 YAML 预设，逐个转为
    per-user Task 行插入 tasks_book（SOURCE_PROACTIVE）。
    """
    try:
        contact = bus.contacts_book.get(contact_id=job.contact_id)
        if contact is None:
            _submit_failure(bus, job, f"contact {job.contact_id} not found")
            return

        if contact.role != "assigned":
            _submit_success(bus, job, inserted=0, skipped=0)
            return

        presets = _load_presets(bus)
        if not presets:
            _submit_success(bus, job, inserted=0, skipped=0)
            return

        contact_label = (
            contact.display_name
            or contact.name
            or f"contact {contact.id}"
        ).strip()
        tz = _read_system_timezone(bus)

        inserted = 0
        skipped = 0
        for preset in sorted(presets.values(), key=lambda p: p.get("key", "")):
            if not preset.get("enabled", True):
                continue

            # 构建 cron / run_at
            frequency = str(preset.get("frequency") or "")
            if frequency == "once":
                raw_run_at = preset.get("run_at")
                if not raw_run_at:
                    skipped += 1
                    continue
                try:
                    run_at_iso = validate_run_at(raw_run_at)
                except ValueError:
                    skipped += 1
                    continue
                cron_val = ""
                run_at_val = run_at_iso
            elif frequency in ("hourly", "daily", "weekly", "monthly"):
                try:
                    cron_val = preset_to_cron(
                        frequency,
                        hour=int(preset.get("hour") or 0),
                        minute=int(preset.get("minute") or 0),
                        day_of_week=preset.get("day_of_week"),
                        day_of_month=preset.get("day_of_month"),
                    )
                except (ValueError, TypeError):
                    skipped += 1
                    continue
                run_at_val = None
            else:
                # YAML 里写了非法的 frequency (e.g. "yearly"、拼错的 "weakly") —
                # ``preset_to_cron`` 不识别会悄悄返回 None，预先跳过。
                skipped += 1
                continue

            task_name = f"{preset.get('name', '')} ({contact_label})"

            # 幂等：已存在同名的 Task 且属于同一 contact
            existing = bus.tasks_book.get_by_name(name=task_name)
            if existing is not None and existing.uid == job.contact_id:
                skipped += 1
                continue

            try:
                kwargs: dict = dict(
                    name=task_name,
                    prompt=str(preset.get("prompt") or ""),
                    target_channel=str(preset.get("channel") or "webui"),
                    uid=job.contact_id,
                    tz=tz,
                    source=SOURCE_PROACTIVE,
                    enabled=1,
                )
                if cron_val:
                    kwargs["cron"] = cron_val
                else:
                    kwargs["run_at"] = run_at_val
                bus.tasks_book.add(**kwargs)
                inserted += 1
            except ValueError:
                skipped += 1

        _submit_success(bus, job, inserted=inserted, skipped=skipped)

    except Exception as exc:
        logger.exception("preset_tasks: seed job %s failed", job.job_id)
        _submit_failure(bus, job, str(exc))


# --- helpers -----------------------------------------------------------------


def _load_presets(bus: "Bus") -> dict:
    """Read bundled preset templates from prompt_book."""
    try:
        return bus.prompt_book.task_presets()
    except Exception:
        logger.warning("preset_tasks: failed to read presets from prompt_book")
        return {}


def _read_system_timezone(bus: "Bus") -> str:
    try:
        raw = bus.settings_book.get(key="system.timezone")
        if raw and isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception:
        pass
    return "UTC"


def _submit_success(
    bus: "Bus", job: "SeedPresetTasksJob",
    *, inserted: int, skipped: int,
) -> None:
    try:
        result = SeedPresetTasksResult(
            job_id=job.job_id, success=True,
            inserted=inserted, skipped=skipped,
        )
        bus.seed_preset_tasks_job_board.submit_result(key=job.job_id, result=result)
    except Exception:
        # Mirror _submit_failure: a result-submission error must not
        # propagate out of the Worker — the preset rows are already
        # committed, and the lease will time out + base will mark the
        # row exhausted if we lose the result write.
        logger.exception(
            "preset_tasks: failed to submit seed success for %s", job.job_id,
        )


def _submit_failure(
    bus: "Bus", job: "SeedPresetTasksJob", error: str,
) -> None:
    try:
        result = SeedPresetTasksResult(
            job_id=job.job_id, success=False, error=error[:8000],
        )
        bus.seed_preset_tasks_job_board.submit_result(
            key=job.job_id, result=result,
        )
    except Exception:
        logger.exception(
            "preset_tasks: failed to submit seed failure for %s", job.job_id,
        )
