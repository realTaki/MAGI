"""Bundled scheduled-task templates and their database synchroniser.

The templates live in ``magi/agent/prompts/task_presets/*.yaml`` so the
natural-language instructions can be tuned and reviewed like every other
prompt.  ``task_presets`` remains the runtime-facing table used by the API
and per-contact seeder, but bundled rows are synchronised from these files
whenever the node initialises its ORM.

Template keys are the ownership boundary: a row whose key is bundled is
updated on boot; rows created by an operator with another key remain entirely
database-owned.  A bundled template deliberately wins over a WebUI edit on a
later restart, keeping deployed default behaviour reproducible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from magi.agent.proactive.orm_models import TaskPreset

logger = logging.getLogger("magi.agent.proactive.preset_templates")

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "prompts" / "task_presets"
_REQUIRED_FIELDS = frozenset({
    "id", "key", "name", "description", "prompt", "frequency", "hour",
    "minute", "day_of_week", "day_of_month", "run_at", "channel", "enabled",
})
_SYNCED_FIELDS = (
    "name", "description", "prompt", "frequency", "hour", "minute",
    "day_of_week", "day_of_month", "run_at", "target_channel", "enabled",
)


@dataclass(frozen=True, slots=True)
class TaskPresetTemplate:
    """Validated source definition for one bundled ``TaskPreset`` row."""

    id: str
    key: str
    name: str
    description: str
    prompt: str
    frequency: str
    hour: int
    minute: int
    day_of_week: int | None
    day_of_month: int | None
    run_at: str | None
    target_channel: str
    enabled: int


def load_task_preset_templates() -> tuple[TaskPresetTemplate, ...]:
    """Load every bundled task-preset YAML file in stable path order.

    This intentionally does not cache file contents: node initialisation is
    infrequent, and a developer changing a mounted prompt file should see the
    new definition on the next restart without a cache-reset mechanism.
    """
    paths = sorted([*_TEMPLATE_DIR.glob("*.yaml"), *_TEMPLATE_DIR.glob("*.yml")])
    if not paths:
        raise RuntimeError(f"no task preset templates found in {_TEMPLATE_DIR}")

    templates: list[TaskPresetTemplate] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"could not load task preset template {path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("presets"), list):
            raise ValueError(f"{path}: expected a mapping with a 'presets' list")
        for index, raw in enumerate(data["presets"], start=1):
            template = _parse_template(raw, path, index)
            if template.id in seen_ids:
                raise ValueError(f"duplicate task preset id {template.id!r}")
            if template.key in seen_keys:
                raise ValueError(f"duplicate task preset key {template.key!r}")
            seen_ids.add(template.id)
            seen_keys.add(template.key)
            templates.append(template)
    return tuple(templates)


def sync_bundled_task_presets(session: Session) -> tuple[int, int]:
    """Insert or update bundled template rows, returning ``(created, updated)``.

    Callers own the transaction.  The stable ``key`` is used for lookup so a
    pre-existing task's ``preset_id`` foreign key is never rewritten merely
    because a source file's metadata changes.
    """
    templates = load_task_preset_templates()
    existing_by_key = {
        row.key: row
        for row in session.scalars(select(TaskPreset)).all()
    }
    now = datetime.now(timezone.utc).isoformat()
    created = 0
    updated = 0

    for template in templates:
        row = existing_by_key.get(template.key)
        if row is None:
            session.add(TaskPreset(
                id=template.id,
                key=template.key,
                name=template.name,
                description=template.description,
                prompt=template.prompt,
                frequency=template.frequency,
                hour=template.hour,
                minute=template.minute,
                day_of_week=template.day_of_week,
                day_of_month=template.day_of_month,
                run_at=template.run_at,
                target_channel=template.target_channel,
                enabled=template.enabled,
                created_at=now,
                updated_at=now,
            ))
            created += 1
            continue

        if row.id != template.id:
            logger.warning(
                "bundled task preset %s has database id %s, expected %s; preserving id",
                template.key, row.id, template.id,
            )
        changed = False
        for field in _SYNCED_FIELDS:
            value = getattr(template, field)
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        if changed:
            row.updated_at = now
            updated += 1

    if created or updated:
        logger.info("synchronised bundled task presets: created=%d updated=%d", created, updated)
    return created, updated


def _parse_template(raw: Any, path: Path, index: int) -> TaskPresetTemplate:
    label = f"{path}:{index}"
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: each preset must be a mapping")
    unknown = set(raw) - _REQUIRED_FIELDS
    missing = _REQUIRED_FIELDS - set(raw)
    if unknown or missing:
        raise ValueError(f"{label}: unknown fields={sorted(unknown)}, missing fields={sorted(missing)}")

    def string(name: str, *, allow_empty: bool = False) -> str:
        value = raw[name]
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            raise ValueError(f"{label}: {name} must be a non-empty string")
        return value

    def optional_int(name: str) -> int | None:
        value = raw[name]
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{label}: {name} must be an integer or null")
        return value

    hour = optional_int("hour")
    minute = optional_int("minute")
    if hour is None or not 0 <= hour <= 23:
        raise ValueError(f"{label}: hour must be 0..23")
    if minute is None or not 0 <= minute <= 59:
        raise ValueError(f"{label}: minute must be 0..59")
    day_of_week = optional_int("day_of_week")
    day_of_month = optional_int("day_of_month")
    if day_of_week is not None and not 0 <= day_of_week <= 6:
        raise ValueError(f"{label}: day_of_week must be 0..6 or null")
    if day_of_month is not None and not 1 <= day_of_month <= 31:
        raise ValueError(f"{label}: day_of_month must be 1..31 or null")

    frequency = string("frequency")
    if frequency not in {"hourly", "daily", "weekly", "monthly", "once"}:
        raise ValueError(f"{label}: unsupported frequency {frequency!r}")
    target_channel = string("channel")
    if target_channel not in {"webui", "tg"}:
        raise ValueError(f"{label}: unsupported channel {target_channel!r}")
    enabled_raw = raw["enabled"]
    if not isinstance(enabled_raw, bool):
        raise ValueError(f"{label}: enabled must be true or false")

    run_at_raw = raw["run_at"]
    if run_at_raw is not None and not isinstance(run_at_raw, str):
        raise ValueError(f"{label}: run_at must be a string or null")
    if frequency == "once" and not run_at_raw:
        raise ValueError(f"{label}: once presets require run_at")
    if frequency != "once" and run_at_raw is not None:
        raise ValueError(f"{label}: only once presets may set run_at")

    return TaskPresetTemplate(
        id=string("id"), key=string("key"), name=string("name"),
        description=string("description", allow_empty=True), prompt=string("prompt"),
        frequency=frequency, hour=hour, minute=minute,
        day_of_week=day_of_week, day_of_month=day_of_month,
        run_at=run_at_raw, target_channel=target_channel,
        enabled=int(enabled_raw),
    )
