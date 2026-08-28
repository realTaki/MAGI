from magi.launcher import BaseWorker


class PostResultWorker(BaseWorker):
    """Post-result gate worker; slots come from ``requiredSlots.py``."""
