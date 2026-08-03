"""Proactive task-preset policy — pure functions, no ORM.

Dependency chain (per ROADMAP):

    contacts API  →  proactive (this module, policy)  →  bus.task (writes)
                        ↑                                  ↑
                        pure function                   persistence

This module is the **policy layer**.  It decides *what* task rows should
exist for a freshly-assigned contact; it does NOT touch the database.
``bus.task.seed_presets_for_contact`` owns the actual inserts and
passes the bus-owned DTO snapshots back into this module's pure
planner.

The planner is intentionally a pure function:

  * inputs are dataclasses (``TaskPresetView``-shaped, ``ContactView``)
  * outputs are ``PresetSeedPlan`` — a list of ``PresetSeed`` DTOs
    ready to be inserted by the bus
  * no ``Session``, no ``select``, no SQLAlchemy, no I/O

That separation lets the policy evolve (operator-curated templates,
new frequencies, conditional seeding) without dragging the storage
layer into the change.  It also means the boundary test naturally
allows this module — it never imports ``magi.bus.models`` or
``magi.bus.db``; the bus calls in.

Idempotency
-----------

The planner is asked for a plan; the bus checks for existing rows
(``uid + preset_id``) BEFORE inserting, so re-running the policy on
the same contact is a no-op.  The check lives in the bus because it
needs the storage; the policy is pure.

Snapshot semantics
------------------

The per-user ``Task`` row is built from the preset's fields verbatim
(``prompt``, ``frequency`` / moment fields → 5-field ``cron``,
``target_channel``, …).  Editing the preset template later does NOT
rewrite existing per-user rows — they keep their snapshotted config.
New assigned contacts seeded after the edit pick up the new config;
existing ones don't.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from magi.bus.task_schedule import (
    preset_to_cron,
    validate_run_at,
)

logger = logging.getLogger("magi.proactive.task_presets")


# --- DTOs (pure data, no ORM) -----------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskPresetSnapshot:
    """One operator-curated preset template, as the policy sees it.

    Mirrors the bus-side ``TaskPresetView`` columns the policy
    actually needs.  The bus service translates its view rows into
    this dataclass before handing the list to the planner.
    """

    id: str
    key: str
    name: str
    prompt: str
    frequency: str
    hour: int
    minute: int
    day_of_week: Optional[int]
    day_of_month: Optional[int]
    run_at: Optional[str]
    target_channel: str
    enabled: int  # 0 / 1


@dataclass(frozen=True, slots=True)
class ContactSnapshot:
    """One contact, as the policy sees it.

    The bus's ``ContactView`` carries more fields; the policy only
    needs the bits that show up in seeded rows (uid, display name
    for the per-user label disambiguation, role to gate seeding).
    """

    id: int
    name: str
    display_name: Optional[str]
    role: str


@dataclass(frozen=True, slots=True)
class PresetSeed:
    """One planned Task row to be inserted by the bus.

    The bus translates each ``PresetSeed`` into a real ``Task`` row
    inside its own transaction; this DTO carries every value the
    builder needs (cron / run_at already rendered by the planner).
    """

    preset_id: str
    preset_key: str
    name: str
    prompt: str
    cron: str
    run_at: Optional[str]
    target_channel: str
    delivery_to: Optional[str]
    tz: str  # forensic breadcrumb — runtime reads system.timezone, not this


@dataclass(frozen=True, slots=True)
class PresetSeedPlan:
    """The planner's complete output for one contact.

    ``seeds`` is the list of rows to insert; ``skipped`` records
    presets the policy chose not to schedule (e.g. malformed
    ``run_at``) so the bus can log them without losing the
    operator's intent.
    """

    contact_id: int
    seeds: list[PresetSeed] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (preset_key, reason)


# --- Policy entry point ------------------------------------------------------


def plan_presets_for_contact(
    contact: ContactSnapshot,
    presets: list[TaskPresetSnapshot],
    *,
    system_timezone: str = "UTC",
) -> PresetSeedPlan:
    """Decide which ``Task`` rows an assigned contact should own.

    Pure function: no I/O, no globals, deterministic given the same
    inputs.  The bus feeds the ``presets`` list (the operator-enabled
    subset) and ``system_timezone`` (a forensic breadcrumb stamped
    at write time, not consulted by the runtime); the planner
    returns a :class:`PresetSeedPlan` the bus commits.

    The function is **always safe to call**: it never raises for a
    single bad preset — that row is recorded in ``skipped`` instead,
    so one malformed template doesn't block the rest.  Callers can
    surface ``skipped`` in the WebUI's "what just happened" panel.
    """
    if contact.role != "assigned":
        # Caller should gate on this; the second-line guard keeps
        # the helper a no-op even if a future caller forgets the
        # role-transition check.
        return PresetSeedPlan(contact_id=contact.id)

    seeds: list[PresetSeed] = []
    skipped: list[tuple[str, str]] = []
    # Iterate presets in stable key order so the seeded task names
    # line up across runs (helpful for the operator eyeballing the
    # "预设任务" list).
    for preset in sorted(presets, key=lambda p: p.key):
        if not preset.enabled:
            continue
        seed = _build_seed(preset, contact, system_timezone=system_timezone)
        if seed is None:
            skipped.append((preset.key, "invalid scheduling config"))
            continue
        seeds.append(seed)
    return PresetSeedPlan(contact_id=contact.id, seeds=seeds, skipped=skipped)


# --- Helpers -----------------------------------------------------------------


def _build_seed(
    preset: TaskPresetSnapshot,
    contact: ContactSnapshot,
    *,
    system_timezone: str,
) -> Optional[PresetSeed]:
    """Render a preset into a single planned ``Task`` row.

    Returns ``None`` if the preset's scheduling config is invalid
    (logged so the operator can debug).  The snapshotted fields are:

    - ``name``           — preset.name + contact label (disambiguates
                          the per-user name; ``tasks.name`` carries a
                          global UNIQUE constraint, so two assigned
                          users can't both own a row literally named
                          "每日晨报".  Appending the contact's label
                          keeps the preset's identity visible at a
                          glance while preventing the UNIQUE
                          collision.)
    - ``prompt``         — preset.prompt verbatim
    - ``cron``           — rendered via :func:`preset_to_cron`
                          (or empty for ``once``)
    - ``run_at``         — preset.run_at verbatim for ``once``
    - ``target_channel`` — preset.target_channel
    - ``delivery_to``    — ``None`` for the seed path.  ``channel="webui"``
                          ignores this; ``channel="tg"`` falls back
                          to the contact's bound chat id at fire
                          time via the dispatcher.
    - ``tz``             — the operator's current ``system.timezone``,
                          stamped at seed time as a forensic
                          breadcrumb.  The runtime reads
                          ``system.timezone`` on every fire so a
                          later tz change moves the row to the new
                          local schedule without touching this
                          column.
    """
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

    contact_label = (contact.display_name or contact.name or f"contact {contact.id}").strip()
    return PresetSeed(
        preset_id=preset.id,
        preset_key=preset.key,
        name=f"{preset.name} ({contact_label})",
        prompt=preset.prompt,
        cron=cron,
        run_at=run_at_iso,
        target_channel=preset.target_channel,
        delivery_to=None,
        tz=system_timezone,
    )
