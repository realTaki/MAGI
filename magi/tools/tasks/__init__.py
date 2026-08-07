"""Task & action-item tools.

One module per tool — see :mod:`magi.tools.registry` for
the dispatcher that wires them up.

Schedule:

  - :mod:`magi.tools.tasks.schedule` — schedule a task
    for later execution (cron / once / interval).

Action items (per-contact, role-gated with
``ALLOWED_ROLES = {admin, assigned}`` — the LLM-side
menu filter strips them out for other roles; the
in-run :meth:`Tool.gate` on each tool is the
second-layer defence):

  - :mod:`magi.tools.tasks.add_action_item`
  - :mod:`magi.tools.tasks.complete_action_item`
  - :mod:`magi.tools.tasks.list_action_item`
"""