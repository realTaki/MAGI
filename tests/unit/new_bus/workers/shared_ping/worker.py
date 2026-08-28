from magi.new_bus import BaseWorker

from .requiredSlots import REQUIRED_SLOTS


class SharedPingWorker(BaseWorker):
    """Claim-loop shaped worker; slots come from ``requiredSlots.py``."""

    required_slots = REQUIRED_SLOTS
