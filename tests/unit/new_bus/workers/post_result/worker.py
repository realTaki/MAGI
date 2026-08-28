from magi.new_bus import BaseWorker


class PostResultWorker(BaseWorker):
    """Post-result gate worker; slots come from ``requiredSlots.py``."""
