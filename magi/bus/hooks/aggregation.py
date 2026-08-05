"""Decision aggregation across multiple hook handlers.

Implements spec §11:

  1. Sort handlers by ``(priority ASC, hook_id ASC)``.
  2. Iterate.  OBSERVE handlers run unconditionally (they cannot
     block), GATE handlers run until one returns DENY.
  3. A GATE handler that raises / times out is converted to its
     declared :class:`HookFailureMode`:

       - ``FAIL_OPEN`` → treat as ALLOW (and record the error).
       - ``FAIL_CLOSED`` → treat as DENY (and record the error).

  4. OBSERVE handlers that raise / time out are logged but do not
     affect the aggregated decision.
  5. After a GATE-DENY, OBSERVE handlers continue to run so audit
     trails capture the full picture (configurable; first version
     always lets OBSERVE finish).
  6. Final decision:

       - ``DENY`` if any GATE handler produced ``DENY`` (including
         fail-closed errors / timeouts).
       - ``ALLOW`` otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from magi.bus.hooks.contracts import (
    HookAction,
    HookDecision,
    HookFailureMode,
    HookMode,
)
from magi.bus.hooks.registry import RegisteredHandler


@dataclass(frozen=True, slots=True)
class HandlerOutcome:
    """The result of running one handler.

    Carries the original decision (or a synthesized one from a
    fail-closed error) plus the error info for audit.
    """

    handler: RegisteredHandler
    decision: HookDecision
    error_type: str | None = None
    sanitized_error: str | None = None
    timed_out: bool = False


def aggregate_decisions(
    outcomes: tuple[HandlerOutcome, ...],
) -> tuple[HookAction, tuple[HookDecision, ...], tuple[str, ...]]:
    """Aggregate ``outcomes`` into a final :class:`HookAction`.

    Returns ``(action, decisions, reason_codes)`` where:

      - ``action`` is the final verdict (ALLOW / DENY).
      - ``decisions`` is the tuple of :class:`HookDecision` values
        to persist (one per handler that ran, including the
        synthesized ones from fail-closed errors).
      - ``reason_codes`` is the deduplicated list of reason codes
        in evaluation order; useful for logging / metrics.

    The function never mutates ``outcomes``.
    """
    if not outcomes:
        return HookAction.ALLOW, (), ()

    decisions: list[HookDecision] = []
    reason_codes: list[str] = []
    saw_deny = False

    for outcome in outcomes:
        decisions.append(outcome.decision)
        if outcome.decision.action is HookAction.DENY:
            saw_deny = True
            if outcome.decision.reason_code:
                reason_codes.append(outcome.decision.reason_code)

    action = HookAction.DENY if saw_deny else HookAction.ALLOW
    return action, tuple(decisions), tuple(reason_codes)


def synthesize_decision_from_error(
    handler: RegisteredHandler,
    *,
    hook_event_id: str,
    error_type: str,
    sanitized_error: str,
    timed_out: bool = False,
) -> HookDecision:
    """Return the decision a handler should produce when it failed.

    The synthesized decision honours the handler's declared
    :class:`HookFailureMode`:

      - ``FAIL_OPEN`` → ``ALLOW`` with the error recorded as
        labels / metadata.
      - ``FAIL_CLOSED`` → ``DENY`` with the error recorded as
        the ``reason_code`` (``hook.handler_timeout`` or
        ``hook.handler_error``).

    For OBSERVE handlers this function still returns a
    :class:`HookDecision` so the executor can persist it
    uniformly; the aggregation step ignores OBSERVE outcomes when
    computing the final action.
    """
    is_gate = handler.registration.mode is HookMode.GATE
    fail_closed = (
        is_gate
        and handler.registration.failure_mode is HookFailureMode.FAIL_CLOSED
    )

    if fail_closed:
        action = HookAction.DENY
        reason_code = "hook.handler_timeout" if timed_out else "hook.handler_error"
    else:
        action = HookAction.ALLOW
        reason_code = None

    return HookDecision(
        hook_id=handler.registration.hook_id,
        hook_version=handler.registration.hook_version,
        hook_event_id=hook_event_id,
        action=action,
        reason_code=reason_code,
        message=sanitized_error,
        labels=("timeout",) if timed_out else ("error",),
        metadata={
            "error_type": error_type,
            "timed_out": timed_out,
            "failure_mode": handler.registration.failure_mode.value,
        },
    )


__all__ = [
    "HandlerOutcome",
    "aggregate_decisions",
    "synthesize_decision_from_error",
]
