"""Contact directory tools.

LLM-managed directory of people the MAGI knows about.
One module per tool — see :mod:`tools.registry` for
the dispatcher that wires them up.

  - :mod:`tools.memory.contacts.add_contact`
  - :mod:`tools.memory.contacts.add_contact_note`
  - :mod:`tools.memory.contacts.update_contact_note`
  - :mod:`tools.memory.contacts.delete_contact_note`
  - :mod:`tools.memory.contacts.update_daily_note`
  - :mod:`tools.memory.contacts.search_contacts`

Notes are individual rows in ``contact_notes`` — each
call to ``add_contact_note`` creates one row. The agent
can update or delete individual notes by id without
rewriting everything else about the same person.
"""
