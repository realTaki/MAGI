"""MAGI memory subsystem — three layers, three purposes.

  - :mod:`.magi`     — MAGI's own mid-term memory
                        (important facts + ongoing work).
                        Renders into the LLM system prompt.
  - :mod:`.contacts`  — the contact directory; what the
                        MAGI knows about people. The current
                        chatter's contact renders into the
                        system prompt; the rest is tool-
                        loaded on demand.
  - :mod:`.session`   — short-term + long-term conversation
                        history. The active tail + summary
                        renders into the LLM message stream;
                        the archive is tool-loaded on demand.

The split keeps each layer's lifecycle + prompt rules
in their own file. None of the three layers talk to
each other at the SQL level (they all share ``Base``
from :mod:`magi.agent.db` but no inter-table FKs); the
only cross-cutting dependency is the agent loop's
prompt assembly, which renders each layer's formatter
in a fixed order.
"""

from __future__ import annotations

# Re-export the three sub-packages so callers can do
# ``from magi.agent.memory import magi, contacts, session``
# and reach every symbol in one import. The sub-package
# ``__init__.py`` files own their own public surface
# (each lists its own __all__).
from magi.agent.memory import contacts, magi, session  # noqa: F401
