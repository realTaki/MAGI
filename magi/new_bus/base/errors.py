"""BUS error model — small and direct."""

__all__ = [
    "BackendError",
    "BookNotFoundError",
    "BusError",
    "InvalidJobError",
    "InvalidJobStateError",
    "JobNotFoundError",
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
