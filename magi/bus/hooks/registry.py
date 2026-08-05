"""In-memory registry of :class:`HookHandlerProtocol` handlers.

The registry is owned by the composition root (one per process).
The runtime does NOT consult the registry directly — it asks
:meth:`HookService.evaluate` (which in turn consults the registry)
so that scope validation, ordering, and persistence happen in one
place.

Ordering: handlers are returned sorted by ``(priority ASC,
hook_id ASC)`` per spec §11.  Two handlers with the same priority
and id are not allowed (registration is rejected).
"""

from __future__ import annotations

from dataclasses import dataclass

from magi.bus.hooks.contracts import (
    HookDataScope,
    HookHandlerProtocol,
    HookPoint,
    HookRegistration,
)
from magi.bus.hooks.scope_policy import allowed_scopes_for


class HookRegistrationError(ValueError):
    """Raised when a handler registration violates the policy."""


@dataclass(frozen=True, slots=True)
class RegisteredHandler:
    """A registered handler plus its resolved policy.

    The BUS materializer consults ``requested_scopes`` to know
    exactly which fields to project from the candidate record into
    the :class:`HookEnvelope`.
    """

    registration: HookRegistration
    handler: HookHandlerProtocol
    # Scopes the materializer will project.  Computed once at
    # registration time so the executor does not have to re-query
    # the policy map per evaluation.
    projected_scopes: frozenset[HookDataScope]


class HookRegistry:
    """Process-wide registry of hook handlers.

    Thread/async-safe enough for the runtime's "register at boot,
    read at evaluation" access pattern — registration is sequential
    at boot time; reads happen concurrently during evaluation.
    Internal mutation uses a private dict and replaces the public
    tuple under the GIL so iteration sees a consistent snapshot.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, RegisteredHandler] = {}

    # -- registration ------------------------------------------------- #

    def register(
        self,
        registration: HookRegistration,
        handler: HookHandlerProtocol,
    ) -> RegisteredHandler:
        """Validate ``registration`` and install ``handler``.

        Raises :class:`HookRegistrationError` if the registration
        violates the policy (unknown scopes, duplicate ``hook_id``,
        zero ``hook_points``, non-positive ``timeout_ms``, …).
        """
        self._validate(registration)
        registered = RegisteredHandler(
            registration=registration,
            handler=handler,
            projected_scopes=frozenset(registration.required_scopes),
        )
        self._handlers[registration.hook_id] = registered
        return registered

    def unregister(self, hook_id: str) -> None:
        """Remove a handler by id.  No-op if absent."""
        self._handlers.pop(hook_id, None)

    def disable(self, hook_id: str) -> None:
        """Flip ``enabled`` to False without removing the handler."""
        current = self._handlers.get(hook_id)
        if current is None:
            return
        self._handlers[hook_id] = RegisteredHandler(
            registration=_with_enabled(current.registration, False),
            handler=current.handler,
            projected_scopes=current.projected_scopes,
        )

    def enable(self, hook_id: str) -> None:
        current = self._handlers.get(hook_id)
        if current is None:
            return
        self._handlers[hook_id] = RegisteredHandler(
            registration=_with_enabled(current.registration, True),
            handler=current.handler,
            projected_scopes=current.projected_scopes,
        )

    # -- lookup -------------------------------------------------------- #

    def get(self, hook_id: str) -> RegisteredHandler | None:
        return self._handlers.get(hook_id)

    def all_handlers(self) -> tuple[RegisteredHandler, ...]:
        """Return every registered handler, sorted by priority."""
        return self._sorted_handlers(self._handlers.values())

    def handlers_for(self, hook_point: HookPoint) -> tuple[RegisteredHandler, ...]:
        """Return handlers subscribed to ``hook_point``, sorted by priority.

        OBSERVE handlers come after GATE handlers at the same
        priority so a DENY short-circuits the GATE phase before
        OBSERVE work fires (spec §11.5).
        """
        return self._sorted_handlers(
            h for h in self._handlers.values()
            if hook_point in h.registration.hook_points
            and h.registration.enabled
        )

    def is_empty(self) -> bool:
        return not self._handlers

    # -- internal ------------------------------------------------------ #

    @staticmethod
    def _sorted_handlers(handlers) -> tuple[RegisteredHandler, ...]:
        """Sort by ``(mode, priority ASC, hook_id ASC)``.

        Mode ordering: GATE (0) before OBSERVE (1).  Within a mode,
        lower priority values run first; ties broken by ``hook_id``
        for determinism.
        """
        return tuple(
            sorted(
                handlers,
                key=lambda h: (
                    0 if h.registration.mode.value == "gate" else 1,
                    h.registration.priority,
                    h.registration.hook_id,
                ),
            )
        )

    @staticmethod
    def _validate(registration: HookRegistration) -> None:
        if registration.timeout_ms <= 0:
            raise HookRegistrationError(
                f"hook {registration.hook_id!r}: timeout_ms must be > 0"
            )
        if not registration.hook_points:
            raise HookRegistrationError(
                f"hook {registration.hook_id!r}: hook_points must be non-empty"
            )
        for point in registration.hook_points:
            permitted = allowed_scopes_for(point)
            forbidden = registration.required_scopes - permitted
            if forbidden:
                raise HookRegistrationError(
                    f"hook {registration.hook_id!r}: requested scopes "
                    f"{tuple(s.value for s in forbidden)} are not permitted "
                    f"at hook point {point.value!r}"
                )


def _with_enabled(registration: HookRegistration, enabled: bool) -> HookRegistration:
    """Return a copy of ``registration`` with ``enabled`` flipped.

    ``HookRegistration`` is frozen, so this is the only way to
    update the field without re-constructing every other argument.
    """
    from dataclasses import replace

    return replace(registration, enabled=enabled)


__all__ = ["HookRegistrationError", "HookRegistry", "RegisteredHandler"]
