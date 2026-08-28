from magi.new_bus import BaseWorker


class SharedPingWorker(BaseWorker):
    """Claim-loop shaped worker; slots come from ``requiredSlots.py``."""
