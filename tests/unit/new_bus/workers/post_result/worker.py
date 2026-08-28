from magi.new_bus import BaseWorker

from .requiredSlots import REQUIRED_SLOTS


class PostResultWorker(BaseWorker):
    """Post-result gate worker; slots come from ``requiredSlots.py``."""

    required_slots = REQUIRED_SLOTS
