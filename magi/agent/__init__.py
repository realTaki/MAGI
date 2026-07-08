"""MAGI runtime — shared between Adam and EVE.

Holds the agent loop, dynamic context builder, skill runner,
proactive engine, LLM provider abstraction and audit writers.
Both node types run this package; the only differences live in
(a) which ``channels.*`` adapter is mounted, (b) the
permission scope, and (c) where each node sources its data
from.

Filled in across C3-C8. The package exists now so subsequent
checkpoints import against a stable layout.

Public surface (re-exported from :mod:`magi.agent.loop`):

- :func:`handle_message` — the agent loop entry point. WebUI
  / TG / proactive callers all reach it through
  ``magi.agent.handle_message``.
- :func:`_record_token_usage` / :func:`_build_messages_from_session`
  / :func:`_maybe_compact` — internal helpers exposed to
  tests; loop.py is the canonical home.
"""

from __future__ import annotations


# The agent loop is a sizeable module (~760 lines). Keep it
# out of the package-import path until someone touches a
# symbol that needs it — importing ``magi.agent`` should stay
# cheap. Lazy re-export via __getattr__ (PEP 562) means
# ``from magi.agent import handle_message`` still works.
#
# Symbols re-exported: every public + ``_``-prefixed name
# defined in :mod:`magi.agent.loop`. The set is open-ended
# so we don't enumerate it here; passing through
# ``dir(loop_mod)`` keeps it future-proof without the
# caller having to update this file every time loop.py
# grows a new helper.

def __getattr__(name: str):
    import magi.agent.loop as _loop
    try:
        return getattr(_loop, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )


def __dir__():
    import magi.agent.loop as _loop
    return sorted(set(globals().keys()) | set(dir(_loop)))