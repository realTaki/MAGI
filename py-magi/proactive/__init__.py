"""System-level proactive intelligence.

This package is intentionally separate from ``channels.tasks``;
the latter executes an operator-defined schedule, this package
runs system-level proactive policies as a durable Worker.

The two currently implemented policies:

- **Credentials nudge** — inserted by the Worker at start-up for
  every admin of the MAGIS when this MAGI is its ADAM (see
  :meth:`proactive.worker.ProactiveWorker._bootstrap`).
  The spec + idempotent-insert helper live in
  :mod:`proactive.credentials_action`.
- **Preset task seeding** — :class:`proactive.worker.ProactiveWorker`
  drains :class:`bus.firmwares.jobs.seedPresetTasksJob.SeedPresetTaskJob`
  rows (one job per preset; one Task per job) via
  :mod:`proactive.preset_tasks`, which reads bundled Markdown records
  from :class:`~bus.firmwares.books.file.promptBook.PromptBook`, runs the pure
  planner, and inserts per-user Task rows.
"""

from proactive.credentials_action import (
    CREDENTIALS_NUDGE,
    CredentialsNudgeSpec,
    ensure_for_admin,
)
from proactive.worker import ProactiveWorker

__all__ = [
    "CREDENTIALS_NUDGE",
    "CredentialsNudgeSpec",
    "ensure_for_admin",
    "ProactiveWorker",
]
