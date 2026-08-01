"""Scheduled-task channel.

``Channel.SCHEDULED`` is an internal trigger channel: it turns a persisted
task schedule into a normal invocation of the MAGI agent loop.  It is not a
user-facing transport, so it has no receive socket or standalone credentials.

The package owns task persistence, schedule validation, CRUD-facing execution
and scheduler lifecycle. System-initiated proactive policies, including
bundled task presets and their seeding, live in :mod:`magi.proactive`.
"""

from magi.channels.tasks.channel import TaskChannel

__all__ = ["TaskChannel"]
