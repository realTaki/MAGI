"""System-level proactive intelligence.

This package is intentionally separate from ``magi.channels.tasks``.  The
latter executes an operator-defined schedule; this package will eventually
observe system signals, decide whether intervention is warranted, and request
an action through a channel.  Heartbeats, policy evaluation and autonomous
planners are not implemented yet, so importing this package has no side
effects and starts no background work.

The currently implemented proactive policy is task presets: it can materialise
ordinary scheduled tasks, while their CRUD, timing and execution remain in the
task channel. ``contracts`` provides the stable boundary for future components.
"""

from magi.proactive.contracts import ProactiveSignal, ProposedAction
from magi.proactive.credentials_nudge import (
    CREDENTIALS_NUDGE,
    CredentialsNudgeSpec,
    ensure_for_admin,
)

__all__ = [
    "ProactiveSignal",
    "ProposedAction",
    "CREDENTIALS_NUDGE",
    "CredentialsNudgeSpec",
    "ensure_for_admin",
]
