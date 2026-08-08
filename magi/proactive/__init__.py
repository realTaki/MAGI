"""System-level proactive intelligence.

This package is intentionally separate from ``magi.channels.tasks``;
the latter executes an operator-defined schedule, this package
runs system-level proactive policies as a durable Worker.

The two currently implemented policies:

- **Credentials nudge** — inserted by the Worker at start-up for
  every admin of the MAGIS when this MAGI is its ADAM (see
  :meth:`magi.proactive.worker.ProactiveWorker._bootstrap`).
  :func:`ensure_for_admin` is a thin shim kept for the
  onboarding API's synchronous path; the Worker is the new
  authority and the function will be retired when the
  onboarding flow publishes its own Job (see
  ``docs/design/proactive-refactor.md`` §6.2).
- **Preset task seeding** — :class:`magi.proactive.worker.ProactiveWorker`
  drains :class:`magi.new_bus.guild.seedPresetTasksJob.SeedPresetTasksJob`
  rows and runs the pure planner
  :func:`magi.proactive.task_presets.plan_presets_for_contact` against
  the per-contact snapshot.

Refactor note (2026-08): the proactive layer now runs as a Worker,
not as inline API calls.  The Worker is the last to start so every
other subsystem is ready before it evaluates Adam status and drains
``SeedPresetTasksJob`` rows.  The legacy ``contracts.py`` and
``credentials_nudge.py`` modules have been deleted — the
credentials-nudge spec now lives next to the Worker that owns the
policy.  See ``docs/design/proactive-refactor.md`` for the full plan.
"""

from magi.proactive.worker import (
    CREDENTIALS_NUDGE,
    CredentialsNudgeSpec,
    ProactiveWorker,
    ensure_for_admin,
    start_proactive_worker,
    stop_proactive_worker,
)

__all__ = [
    "CREDENTIALS_NUDGE",
    "CredentialsNudgeSpec",
    "ProactiveWorker",
    "ensure_for_admin",
    "start_proactive_worker",
    "stop_proactive_worker",
]
