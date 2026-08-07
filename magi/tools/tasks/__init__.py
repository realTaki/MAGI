"""Task & action-item tools.

  - :mod:`magi.tools.tasks.schedule` — schedule a task
    for later execution (cron / once / interval).
  - :mod:`magi.tools.tasks.action_item` — per-contact
    action items; ``ALLOWED_ROLES = {admin, assigned}``
    keeps the menu filtered for other roles; the
    in-run ``_gate`` on each tool is the second-layer
    defence.
"""