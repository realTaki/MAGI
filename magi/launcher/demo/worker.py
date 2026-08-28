"""Demo worker used to exercise Launcher topology and lifecycle."""

from magi.new_bus import BaseWorker


class DemoWorker(BaseWorker):
    """Holds ``CreateConversationJob.publish``; see ``requiredSlots.py``."""
