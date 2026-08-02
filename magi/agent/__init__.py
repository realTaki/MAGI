"""MAGI runtime — shared between Adam and EVE.

Holds the agent loop, dynamic context builder, session/memory handling,
LLM provider abstraction and audit writers. Tool implementations, Skills and
MCP integration live in the separate :mod:`magi.tools` capability layer.
Both node types run this package; the only differences live in
(a) which ``channels.*`` adapter is mounted, (b) the
permission scope, and (c) where each node sources its data
from.

Filled in across C3-C8. The package exists now so subsequent
checkpoints import against a stable layout.

Runtime channels publish to :mod:`magi.bus`; the actor worker owns one
provider step at a time.  Legacy loop helpers remain isolated in
``magi.agent.loop`` only while historical unit tests are migrated, and are not
part of the runtime entry point.
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
