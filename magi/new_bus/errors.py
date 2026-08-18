"""BUS error model — small and direct."""


class BusError(Exception):
    """Base class for every BUS error."""


class BackendError(BusError):
    """Storage backend failed."""


class BookNotFoundError(BusError):
    """A Book record referenced by a Job does not exist."""


class JobNotFoundError(BusError):
    """No Job with the given id exists on that board."""


class InvalidJobError(BusError):
    """Job payload, type, or routing is not valid."""


class InvalidJobStateError(BusError):
    """The requested transition is not allowed from the Job's current status."""


class JobAlreadyClaimedError(BusError):
    """A claim lost the one-time ownership race."""


class SlotNotFoundError(BusError):
    """Unknown slot name, or detach target is not bound."""


class SlotOccupiedError(BusError):
    """A SINGLE slot already has a handler."""


class SlotRejected(BusError):
    """A SINGLE pre_* handler aborted the operation."""


class FirmwareCompatibilityError(BusError):
    """Reserved for Firmware protocol checks. Unused in Base."""
