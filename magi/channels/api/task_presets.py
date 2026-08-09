"""``/api/task-presets`` — manage the preset templates.

The :class:`TaskPreset` table is the runtime store for proactive
task templates. Bundled template keys are synchronised from
``magi/prompts/task_presets`` at startup; operator-created
keys are database-owned. This router surfaces the four canonical
CRUD verbs so the Settings → 任务预设 card can list, edit, create,
and delete presets without going through the underlying
``task_presets`` table directly.

Snapshot semantics
------------------

Editing a preset (``PATCH /api/task-presets/{id}``) does NOT
rewrite existing per-user ``Task`` rows. The per-user row
keeps the prompt / schedule / channel it was seeded with;
new assigned contacts seeded AFTER the edit pick up the
new config. For a bundled key, the source YAML takes effect
again after the next node restart; use a newly created key for
a durable operator-owned variant. The edit form in the UI
displays a caveat banner so the operator isn't surprised.

Deleting a preset (``DELETE /api/task-presets/{id}``) sets
``task.preset_id`` to NULL on every per-user row (FK
``ON DELETE SET NULL``). The row's ``preset_key`` stays
populated, so the WebUI's "preset vs custom" split is
unchanged — the surviving rows still render under "预设
任务" until the operator individually disables / deletes
them.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from magi.channels.api._bus import bus
from magi.new_bus.library.local.tasksBook import (
    preset_to_cron,
    validate_run_at,
)
from magi.channels.api.auth_gates import AdminGate

logger = logging.getLogger("magi.api.task_presets")

router = APIRouter(tags=["task-presets"])


# ──────────────────────────────────────────────────────────────────────── #
# Pydantic shapes
# ──────────────────────────────────────────────────────────────────────── #


class TaskPresetOut(BaseModel):
    id: str
    key: str
    name: str
    description: str
    prompt: str
    frequency: str
    hour: int
    minute: int
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    run_at: Optional[str] = None
    target_channel: str
    enabled: bool
    created_at: str
    updated_at: str


class TaskPresetPatch(BaseModel):
    """Partial update — every field optional.

    The operator can rename, retune the schedule, change the
    target channel, swap the prompt, or flip the global
    enable flag. ``key`` is immutable (it's the per-user
    snapshot identifier); changing it would orphan every
    existing per-user row. Editing ``key`` is a 400.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    prompt: Optional[str] = Field(default=None, min_length=1, max_length=8000)
    frequency: Optional[Literal[
        "hourly", "daily", "weekly", "monthly", "once",
    ]] = None
    hour: Optional[int] = Field(default=None, ge=0, le=23)
    minute: Optional[int] = Field(default=None, ge=0, le=59)
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)
    day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    run_at: Optional[str] = None
    target_channel: Optional[Literal["webui", "tg"]] = None
    enabled: Optional[bool] = None


class TaskPresetIn(BaseModel):
    """Create payload — same shape as the patch with the
    added ``key`` field.

    ``key`` must be a stable, machine-readable identifier
    (lowercase + underscores). It becomes the per-user
    ``Task.preset_key`` snapshot; renaming it later is not
    supported (the snapshot is immutable).
    """

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    prompt: str = Field(min_length=1, max_length=8000)
    frequency: Literal["hourly", "daily", "weekly", "monthly", "once"]
    hour: int = Field(default=0, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)
    day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    run_at: Optional[str] = None
    target_channel: Literal["webui", "tg"] = "webui"
    enabled: bool = True


# ──────────────────────────────────────────────────────────────────────── #
# ORM → Pydantic
# ──────────────────────────────────────────────────────────────────────── #


def _preset_to_out(p) -> TaskPresetOut:
    return TaskPresetOut(
        id=p.id,
        key=p.key,
        name=p.name,
        description=p.description,
        prompt=p.prompt,
        frequency=p.frequency,
        hour=p.hour,
        minute=p.minute,
        day_of_week=p.day_of_week,
        day_of_month=p.day_of_month,
        run_at=p.run_at,
        target_channel=p.target_channel,
        enabled=bool(p.enabled),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _bus():
    return bus


# ──────────────────────────────────────────────────────────────────────── #
# Routes
# ──────────────────────────────────────────────────────────────────────── #


@router.get("/task-presets", response_model=list[TaskPresetOut])
def list_task_presets(
    _admin: AdminGate,
) -> list[TaskPresetOut]:
    """List every preset (enabled or not).

    The Settings card uses the full list so the operator
    can see disabled templates and re-enable them. Sorted
    by ``key`` for a stable order.
    """
    return [_preset_to_out(preset) for preset in _bus().task.list_presets()]


@router.post(
    "/task-presets",
    response_model=TaskPresetOut,
    status_code=201,
)
def create_task_preset(
    payload: TaskPresetIn,
    _admin: AdminGate,
) -> TaskPresetOut:
    """Create a new preset template.

    409 if a preset with the same ``key`` already exists.
    The seed helper iterates all enabled presets on every
    assigned-user creation, so the new template is
    immediately available for future seeds.
    """
    from magi.channels.api.errors import MagiHTTPException

    # Validate the scheduling config eagerly so a 400
    # surfaces at create-time rather than as a cryptic
    # scheduler error at the next fire.
    _validate_scheduling(
        frequency=payload.frequency,
        hour=payload.hour,
        minute=payload.minute,
        day_of_week=payload.day_of_week,
        day_of_month=payload.day_of_month,
        run_at=payload.run_at,
    )
    values = payload.model_dump()
    values["run_at"] = _canonicalise_run_at(payload.frequency, payload.run_at)
    preset = _bus().task.create_preset(**values)
    if preset is None:
        raise MagiHTTPException(
            status_code=409, code="task_presets.key_conflict",
            detail=f"a preset with key {payload.key!r} already exists",
        )
    return _preset_to_out(preset)


@router.patch("/task-presets/{preset_id}", response_model=TaskPresetOut)
def update_task_preset(
    preset_id: str,
    payload: TaskPresetPatch,
    _admin: AdminGate,
) -> TaskPresetOut:
    """Edit a preset template.

    Existing per-user ``Task`` rows are NOT updated (snapshot
    semantics). The edit takes effect for *future* seeds.
    The UI surfaces a caveat so the operator isn't surprised
    that the seeded MAGI's existing 每日晨报 task still has
    the old prompt after they edit the template.
    """
    from magi.channels.api.errors import MagiHTTPException

    service = _bus().task
    preset = service.get_preset(preset_id)
    if preset is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.task_preset",
            detail=f"preset {preset_id} not found",
        )
    # Apply the patch fields one at a time — Pydantic's
    # ``exclude_unset`` tells us which keys the operator
    # actually sent. ``key`` is immutable (would orphan
    # every per-user snapshot).
    data = payload.model_dump(exclude_unset=True)
    if "frequency" in data or "hour" in data or "minute" in data or \
       "day_of_week" in data or "day_of_month" in data or \
       "run_at" in data:
        merged_freq = data.get("frequency", preset.frequency)
        merged_hour = data.get("hour", preset.hour)
        merged_minute = data.get("minute", preset.minute)
        merged_dow = data.get("day_of_week", preset.day_of_week)
        merged_dom = data.get("day_of_month", preset.day_of_month)
        merged_run_at = data.get("run_at", preset.run_at)
        _validate_scheduling(
            frequency=merged_freq,
            hour=merged_hour,
            minute=merged_minute,
            day_of_week=merged_dow,
            day_of_month=merged_dom,
            run_at=merged_run_at,
        )
        # Re-canonicalise run_at so the stored shape is
        # consistent (the form sends naive ISO; the
        # canonicalised form carries +00:00).
        if "run_at" in data or "frequency" in data:
            data["run_at"] = _canonicalise_run_at(
                merged_freq, merged_run_at,
            )
    preset = service.update_preset(preset_id, **data)
    if preset is None:  # deleted after the initial read
        raise MagiHTTPException(
            status_code=404, code="not_found.task_preset",
            detail=f"preset {preset_id} not found",
        )
    return _preset_to_out(preset)


@router.delete("/task-presets/{preset_id}", status_code=204)
def delete_task_preset(
    preset_id: str,
    _admin: AdminGate,
) -> None:
    """Remove a preset template.

    Per-user ``Task`` rows that were seeded from this preset
    keep their snapshotted config (FK ``ON DELETE SET NULL``
    on ``tasks.preset_id`` drops the back-pointer; the
    immutable ``preset_key`` column keeps the row in the
    "preset" list until the operator deletes the rows one
    by one). The template won't seed new assigned contacts
    after deletion.
    """
    from magi.channels.api.errors import MagiHTTPException

    if not _bus().task.delete_preset(preset_id):
        raise MagiHTTPException(
            status_code=404, code="not_found.task_preset",
            detail=f"preset {preset_id} not found",
        )
    return None


# ──────────────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────────────── #


def _validate_scheduling(
    *,
    frequency: str,
    hour: int,
    minute: int,
    day_of_week: Optional[int],
    day_of_month: Optional[int],
    run_at: Optional[str],
) -> None:
    """Surface invalid preset scheduling config as 400.

    Mirrors the per-user ``create_task`` route's checks so
    the operator sees the same error code shape on both
    surfaces. Errors here are caught and re-raised with
    task-preset-specific codes so the frontend can
    distinguish "the per-user form is wrong" from "the
    template is wrong".
    """
    from magi.channels.api.errors import MagiHTTPException

    if frequency == "once":
        if not run_at:
            raise MagiHTTPException(
                status_code=400,
                code="task_presets.run_at_required_for_once",
                detail=(
                    "run_at is required when frequency='once'"
                ),
            )
        try:
            validate_run_at(run_at)
        except ValueError as exc:
            raise MagiHTTPException(
                status_code=400,
                code="task_presets.invalid_run_at",
                detail=str(exc),
            ) from exc
    else:
        if run_at:
            raise MagiHTTPException(
                status_code=400,
                code="task_presets.run_at_only_for_once",
                detail=(
                    f"run_at is set; frequency must be 'once', "
                    f"got {frequency!r}"
                ),
            )
        try:
            preset_to_cron(
                frequency,
                hour=hour,
                minute=minute,
                day_of_week=day_of_week,
                day_of_month=day_of_month,
            )
        except (ValueError, TypeError) as exc:
            raise MagiHTTPException(
                status_code=400,
                code="task_presets.invalid_cron_preset",
                detail=str(exc),
            ) from exc


def _canonicalise_run_at(
    frequency: str, run_at: Optional[str],
) -> Optional[str]:
    """Return the canonical ISO form of ``run_at`` for a
    preset, or ``None`` for non-``once`` frequencies.

    Pulled out of the route bodies so a patch and a create
    call share the same canonicalisation logic. ``None`` is
    returned for non-``once`` so the DB row's run_at stays
    NULL — keeping the (cron vs run_at) one-of invariant
    that the scheduler relies on.
    """
    if frequency != "once":
        return None
    if not run_at:
        return None
    try:
        return validate_run_at(run_at)
    except ValueError:
        return run_at  # let _validate_scheduling's 400 surface it
