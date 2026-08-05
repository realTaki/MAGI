"""Plugin-side hook handler base + decorator.

The plugin side of the BUS hook system is intentionally tiny:

  - The handler implements :class:`HookHandlerProtocol` (an
    ``async handle(HookEnvelope) -> HookDecision | None``).
  - The composition root instantiates the handler class and
    calls :meth:`bus.hooks.register_handler`.
  - The handler MUST NOT import anything beyond the
    ``magi.plugins.hooks`` contract surface; the architecture
    test enforces this.

The :func:`hook_handler` decorator is a convenience that lets
plugin authors write the handler as a plain function and get a
class implementing the protocol:

.. code-block:: python

    @hook_handler(
        hook_id="my_plugin",
        hook_version="1.0.0",
        hook_points=(HookPoint.LLM_REQUEST_PREPARED,),
        mode=HookMode.GATE,
        priority=10,
        required_scopes=frozenset({HookDataScope.LLM_REQUEST}),
        timeout_ms=200,
        failure_mode=HookFailureMode.FAIL_CLOSED,
    )
    async def detect_injection(envelope):
        if "DROP TABLE" in envelope.payload.get("request", {}).get("messages", []):
            return HookDecision(
                hook_id="my_plugin",
                hook_version="1.0.0",
                hook_event_id=envelope.hook_event_id,
                action=HookAction.DENY,
                reason_code="prompt_injection",
            )
        return None
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass

from magi.bus.hooks.contracts import (
    HookDataScope,
    HookDecision,
    HookEnvelope,
    HookFailureMode,
    HookHandlerProtocol,
    HookMode,
    HookPoint,
    HookRegistration,
)


@dataclass(frozen=True, slots=True)
class HookHandler:
    """Class-based handler implementing :class:`HookHandlerProtocol`.

    Plugin authors typically use :func:`hook_handler` to build
    one of these from a plain async function.
    """

    registration: HookRegistration
    handle: Callable[[HookEnvelope], "object | None"]

    async def __call__(self, envelope: HookEnvelope) -> HookDecision | None:
        result = self.handle(envelope)
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[func-returns-value]
        return result  # type: ignore[return-value]


def hook_handler(
    *,
    hook_id: str,
    hook_version: str,
    hook_points: tuple[HookPoint, ...],
    mode: HookMode,
    priority: int,
    required_scopes: frozenset[HookDataScope],
    timeout_ms: int,
    failure_mode: HookFailureMode,
    enabled: bool = True,
) -> Callable[[Callable[[HookEnvelope], "object | None"]], HookHandler]:
    """Wrap an async handler function as a :class:`HookHandler`.

    The returned object satisfies :class:`HookHandlerProtocol` so
    the composition root can register it with
    :meth:`bus.hooks.register_handler`.
    """

    def decorator(func: Callable[[HookEnvelope], "object | None"]) -> HookHandler:
        registration = HookRegistration(
            hook_id=hook_id,
            hook_version=hook_version,
            hook_points=hook_points,
            mode=mode,
            priority=priority,
            required_scopes=required_scopes,
            timeout_ms=timeout_ms,
            failure_mode=failure_mode,
            enabled=enabled,
        )
        return HookHandler(registration=registration, handle=func)

    return decorator


__all__ = ["HookHandler", "hook_handler"]
