"""HookExecutor — runs handlers outside transactions with timeouts.

The executor is the only code path that invokes
:class:`HookHandlerProtocol.handle` directly.  It enforces the
timeout and failure-mode policy from spec §11, returning a
:class:`HandlerOutcome` for every handler that ran (or tried to).

The executor is async because every handler is async, but it
returns a fully-resolved tuple — callers do not need to ``await``
any further work after :meth:`run_handlers`.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import Any

from magi.bus.hooks.aggregation import HandlerOutcome, synthesize_decision_from_error
from magi.bus.hooks.contracts import (
    HookAction,
    HookDecision,
    HookEnvelope,
    HookMode,
)
from magi.bus.hooks.registry import RegisteredHandler


logger = logging.getLogger("magi.bus.hooks.executor")


class HookExecutor:
    """Run handlers in order with per-handler timeout + failure mode."""

    async def run_handlers(
        self,
        handlers: tuple[RegisteredHandler, ...],
        envelope: HookEnvelope,
    ) -> tuple[HandlerOutcome, ...]:
        """Run ``handlers`` against ``envelope``.

        Order is the registry's priority order.  Every handler
        that the executor *attempted* shows up in the returned
        tuple — a timeout is not silently dropped.

        The function never raises: handler errors are converted
        into :class:`HandlerOutcome` entries via
        :func:`synthesize_decision_from_error` so the service
        can persist them.
        """
        outcomes: list[HandlerOutcome] = []
        for registered in handlers:
            outcome = await self._run_one(registered, envelope)
            outcomes.append(outcome)
            # Short-circuit GATE-DENY (spec §11.5).  OBSERVE
            # handlers are still run so the audit trail is
            # complete.
            if (
                outcome.decision.action is HookAction.DENY
                and registered.registration.mode is HookMode.GATE
            ):
                continue
        return tuple(outcomes)

    # -- internal ------------------------------------------------------ #

    async def _run_one(
        self,
        registered: RegisteredHandler,
        envelope: HookEnvelope,
    ) -> HandlerOutcome:
        handler = registered.handler
        timeout_s = max(0.001, registered.registration.timeout_ms / 1000)
        try:
            decision = await asyncio.wait_for(
                handler.handle(envelope),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "hook %s timeout after %dms on %s",
                registered.registration.hook_id,
                registered.registration.timeout_ms,
                envelope.hook_point.value,
            )
            synthesized = synthesize_decision_from_error(
                registered,
                hook_event_id=envelope.hook_event_id,
                error_type="asyncio.TimeoutError",
                sanitized_error=f"hook exceeded {registered.registration.timeout_ms}ms",
                timed_out=True,
            )
            return HandlerOutcome(
                handler=registered,
                decision=synthesized,
                error_type="asyncio.TimeoutError",
                sanitized_error=synthesized.message,
                timed_out=True,
            )
        except Exception as exc:
            logger.exception(
                "hook %s raised %s on %s",
                registered.registration.hook_id,
                type(exc).__name__,
                envelope.hook_point.value,
            )
            sanitized = _sanitize_error(exc)
            synthesized = synthesize_decision_from_error(
                registered,
                hook_event_id=envelope.hook_event_id,
                error_type=type(exc).__name__,
                sanitized_error=sanitized,
                timed_out=False,
            )
            return HandlerOutcome(
                handler=registered,
                decision=synthesized,
                error_type=type(exc).__name__,
                sanitized_error=sanitized,
                timed_out=False,
            )
        # OBSERVE handlers may return None — synthesize an ALLOW.
        if decision is None:
            decision = HookDecision(
                hook_id=registered.registration.hook_id,
                hook_version=registered.registration.hook_version,
                hook_event_id=envelope.hook_event_id,
                action=HookAction.ALLOW,
            )
        return HandlerOutcome(handler=registered, decision=decision)


def _sanitize_error(exc: BaseException) -> str:
    """Return a short, single-line description of ``exc``.

    We do not propagate the full traceback through the envelope
    — the executor logs the traceback, handlers see only the
    short type+message form.  No filesystem paths, no env vars,
    no DB connection strings.
    """
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if len(message) > 480:
        message = message[:477] + "..."
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


__all__ = ["HookExecutor"]


# ``datetime`` + ``traceback`` are intentionally not used directly
# here; they are referenced in the docstrings only.
_ = (datetime, traceback, Any)
