"""Task & action-item tools.

One module per tool — see :mod:`tools.registry` for
the dispatcher that wires them up.

Schedule:

  - :mod:`tools.tasks.schedule` — schedule a task
    for later execution (cron / once / interval).

Action items (per-contact, catalog-filtered with
``ALLOWED_ROLES = {admin, assigned}`` — the LLM-side
menu strips them out for other roles):

  - :mod:`tools.tasks.add_action_item`
  - :mod:`tools.tasks.complete_action_item`
  - :mod:`tools.tasks.list_action_item`
"""
