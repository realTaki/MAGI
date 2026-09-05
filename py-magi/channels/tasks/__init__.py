"""Scheduled-task channel.

``task`` is an internal trigger channel: it turns a persisted
task schedule into a normal invocation of the MAGI agent loop.  It is not a
user-facing transport, so it has no receive socket or standalone credentials.

The package owns only scheduler lifecycle. Task persistence and queries remain
inside Firmware and cross this boundary through JobBoards. System-initiated
proactive policies, including bundled task presets and their seeding, live in
:mod:`proactive`.
"""

from .worker import TaskWorker

__all__ = ["TaskWorker"]
