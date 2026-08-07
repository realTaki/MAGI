"""Contact directory tools.

LLM-managed directory of people the MAGI knows about.
One module per tool — see :mod:`magi.tools.registry` for
the dispatcher that wires them up.

All tools gate on ``ALLOWED_ROLES = {admin, assigned}``;
the LLM-side menu filter strips them out for other
roles and :meth:`Tool.gate` is the second-layer defence.

  - :mod:`magi.tools.memory.contacts.add_contact`
  - :mod:`magi.tools.memory.contacts.add_contact_note`
  - :mod:`magi.tools.memory.contacts.update_contact_note`
  - :mod:`magi.tools.memory.contacts.delete_contact_note`
  - :mod:`magi.tools.memory.contacts.update_daily_note`
  - :mod:`magi.tools.memory.contacts.search_contacts`

Notes are individual rows in ``contact_notes`` — each
call to ``add_contact_note`` creates one row. The agent
can update or delete individual notes by id without
rewriting everything else about the same person.
"""