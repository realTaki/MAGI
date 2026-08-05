"""Unit tests for :class:`HookExecutor`."""

from __future__ import annotations

import asyncio

import pytest

from magi.bus.hooks.contracts import (
    HookAction,
    HookDataScope,
    HookDecision,
    HookEnvelope,
    HookFailureMode,
    HookMode,
    HookPoint,
    HookRegistration,
)
from magi.bus.hooks.executor import HookExecutor
from magi.bus.hooks.registry import RegisteredHandler


class _FastHandler:
    """Returns ALLOW immediately."""

    async def handle(self, envelope: HookEnvelope):
        return HookDecision(
            hook_id=self._hook_id,
            hook_version="1",
            hook_event_id=envelope.hook_event_id,
            action=HookAction.ALLOW,
        )

    def __init__(self, hook_id: str = "fast") -> None:
        self._hook_id = hook_id


class _SlowHandler:
    """Sleeps past the timeout."""

    async def handle(self, envelope: HookEnvelope):
        await asyncio.sleep(10)
        return HookDecision(
            hook_id="slow", hook_version="1",
            hook_event_id=envelope.hook_event_id,
            action=HookAction.ALLOW,
        )


class _DenyHandler:
    """Always DENY."""

    async def handle(self, envelope: HookEnvelope):
        return HookDecision(
            hook_id="deny", hook_version="1",
            hook_event_id=envelope.hook_event_id,
            action=HookAction.DENY,
            reason_code="policy_block",
        )


class _CrashHandler:
    """Always raises."""

    async def handle(self, envelope: HookEnvelope):
        raise RuntimeError("boom")


def _envelope() -> HookEnvelope:
    from datetime import datetime, timezone
    return HookEnvelope(
        schema_version="1.0.0",
        hook_event_id="hkevt_1",
        hook_point=HookPoint.TOOL_CALL_PENDING,
        occurred_at=datetime.now(timezone.utc),
        runtime=None, principal=None, causality=None, subject=None,
        payload={}, context={}, security=None,
    )


def _registered(
    handler,
    *,
    hook_id: str = "h",
    mode: HookMode = HookMode.GATE,
    failure_mode: HookFailureMode = HookFailureMode.FAIL_CLOSED,
    timeout_ms: int = 100,
) -> RegisteredHandler:
    return RegisteredHandler(
        registration=HookRegistration(
            hook_id=hook_id,
            hook_version="1",
            hook_points=(HookPoint.TOOL_CALL_PENDING,),
            mode=mode,
            priority=0,
            required_scopes=frozenset({HookDataScope.RUNTIME_IDENTITY}),
            timeout_ms=timeout_ms,
            failure_mode=failure_mode,
        ),
        handler=handler,
        projected_scopes=frozenset({HookDataScope.RUNTIME_IDENTITY}),
    )


def test_run_handlers_returns_outcomes():
    async def _go():
        ex = HookExecutor()
        handlers = (_registered(_FastHandler()),)
        out = await ex.run_handlers(handlers, _envelope())
        assert len(out) == 1
        assert out[0].decision.action is HookAction.ALLOW
    asyncio.run(_go())


def test_run_handlers_handles_deny():
    async def _go():
        ex = HookExecutor()
        handlers = (_registered(_DenyHandler()),)
        out = await ex.run_handlers(handlers, _envelope())
        assert out[0].decision.action is HookAction.DENY
        assert out[0].decision.reason_code == "policy_block"
    asyncio.run(_go())


def test_timeout_produces_timed_out_outcome_fail_closed():
    async def _go():
        ex = HookExecutor()
        handlers = (_registered(_SlowHandler(), timeout_ms=50),)
        out = await ex.run_handlers(handlers, _envelope())
        assert out[0].timed_out is True
        assert out[0].decision.action is HookAction.DENY
        assert out[0].decision.reason_code == "hook.handler_timeout"
    asyncio.run(_go())


def test_timeout_produces_timed_out_outcome_fail_open():
    async def _go():
        ex = HookExecutor()
        handlers = (
            _registered(
                _SlowHandler(),
                timeout_ms=50,
                failure_mode=HookFailureMode.FAIL_OPEN,
            ),
        )
        out = await ex.run_handlers(handlers, _envelope())
        assert out[0].timed_out is True
        assert out[0].decision.action is HookAction.ALLOW
    asyncio.run(_go())


def test_exception_fail_closed_yields_deny():
    async def _go():
        ex = HookExecutor()
        handlers = (_registered(_CrashHandler()),)
        out = await ex.run_handlers(handlers, _envelope())
        assert out[0].error_type == "RuntimeError"
        assert out[0].decision.action is HookAction.DENY
        assert out[0].decision.reason_code == "hook.handler_error"
    asyncio.run(_go())


def test_observe_handler_returning_none_is_synthesized_to_allow():
    class _None:
        async def handle(self, envelope):
            return None

    async def _go():
        ex = HookExecutor()
        handlers = (
            _registered(_None(), mode=HookMode.OBSERVE),
        )
        out = await ex.run_handlers(handlers, _envelope())
        assert out[0].decision.action is HookAction.ALLOW
    asyncio.run(_go())
