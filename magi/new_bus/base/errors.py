"""BUS error model — small and direct."""

__all__ = [
    "BackendError",
    "BookNotFoundError",
    "BusError",
    "InvalidJobError",
    "InvalidJobStateError",
    "JobNotFoundError",
    "SlotNotFoundError",
    "SlotOccupiedError",
    "SlotRejected",
]


class BusError(Exception):
    """Base class for every BUS error."""


class BackendError(BusError):
    """Storage failed."""


class BookNotFoundError(BusError):
    """A BaseBook record referenced by a Job does not exist."""


class JobNotFoundError(BusError):
    """No Job with the given id exists on that board."""


class InvalidJobError(BusError):
    """Job fields, type, or routing is not valid."""


class InvalidJobStateError(BusError):
    """The requested transition is not allowed from the Job's current status."""


class SlotNotFoundError(BusError):
    """Unknown slot name, or detach target is not bound."""


class SlotOccupiedError(BusError):
    """A SINGLE slot already has a handler."""


class SlotRejected(BusError):
    """A SINGLE pre_* handler aborted the operation."""
