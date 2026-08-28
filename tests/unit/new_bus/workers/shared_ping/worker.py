from magi.launcher import BaseWorker


class SharedPingWorker(BaseWorker):
    """Claim-loop shaped worker; slots come from ``requiredSlots.py``."""
