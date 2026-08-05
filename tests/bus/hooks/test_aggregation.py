"""Unit tests for decision aggregation (spec §11)."""

from __future__ import annotations

from magi.bus.hooks.aggregation import (
    HandlerOutcome,
    aggregate_decisions,
    synthesize_decision_from_error,
)
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
from magi.bus.hooks.registry import RegisteredHandler


class _Stub:
    async def handle(self, envelope: HookEnvelope):
        return None


def _registered(
    hook_id: str = "h1",
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
            priority=10,
            required_scopes=frozenset({HookDataScope.RUNTIME_IDENTITY}),
            timeout_ms=timeout_ms,
            failure_mode=failure_mode,
        ),
        handler=_Stub(),
        projected_scopes=frozenset({HookDataScope.RUNTIME_IDENTITY}),
    )


def _outcome(handler: RegisteredHandler, action: HookAction, reason: str | None = None):
    return HandlerOutcome(
        handler=handler,
        decision=HookDecision(
            hook_id=handler.registration.hook_id,
            hook_version=handler.registration.hook_version,
            hook_event_id="hkevt_test",
            action=action,
            reason_code=reason,
        ),
    )


def test_no_outcomes_returns_allow():
    action, decisions, reasons = aggregate_decisions(())
    assert action is HookAction.ALLOW
    assert decisions == ()
    assert reasons == ()


def test_single_allow_returns_allow():
    h = _registered()
    action, decisions, _ = aggregate_decisions((_outcome(h, HookAction.ALLOW),))
    assert action is HookAction.ALLOW
    assert len(decisions) == 1


def test_single_deny_returns_deny():
    h = _registered()
    action, decisions, reasons = aggregate_decisions(
        (_outcome(h, HookAction.DENY, "policy_block"),)
    )
    assert action is HookAction.DENY
    assert reasons == ("policy_block",)


def test_mixed_observe_and_gate_returns_deny():
    observe = _registered("ob", HookMode.OBSERVE, HookFailureMode.FAIL_OPEN)
    gate = _registered("gt", HookMode.GATE, HookFailureMode.FAIL_CLOSED)
    action, decisions, _ = aggregate_decisions((
        _outcome(observe, HookAction.ALLOW),
        _outcome(gate, HookAction.DENY, "danger"),
    ))
    assert action is HookAction.DENY
    assert len(decisions) == 2


def test_fail_closed_timeout_produces_deny():
    h = _registered("strict", HookMode.GATE, HookFailureMode.FAIL_CLOSED)
    synthesized = synthesize_decision_from_error(
        h, hook_event_id="hkevt_test",
        error_type="asyncio.TimeoutError",
        sanitized_error="100ms timeout", timed_out=True,
    )
    assert synthesized.action is HookAction.DENY
    assert synthesized.reason_code == "hook.handler_timeout"


def test_fail_open_timeout_produces_allow():
    h = _registered(
        "loose", HookMode.GATE, HookFailureMode.FAIL_OPEN,
    )
    synthesized = synthesize_decision_from_error(
        h, hook_event_id="hkevt_test",
        error_type="asyncio.TimeoutError",
        sanitized_error="100ms timeout", timed_out=True,
    )
    assert synthesized.action is HookAction.ALLOW


def test_observe_handler_failure_does_not_block():
    """OBSERVE handlers that fail do not produce DENY."""
    h = _registered("obs", HookMode.OBSERVE, HookFailureMode.FAIL_CLOSED)
    synthesized = synthesize_decision_from_error(
        h, hook_event_id="hkevt_test",
        error_type="RuntimeError", sanitized_error="boom", timed_out=False,
    )
    # Even with fail-closed declared, an OBSERVE failure does
    # not produce DENY because OBSERVE handlers don't affect
    # the aggregated decision (spec §11.4).
    assert synthesized.action is HookAction.ALLOW


def test_multiple_denies_collect_all_reason_codes():
    h1 = _registered("a", HookMode.GATE, HookFailureMode.FAIL_CLOSED)
    h2 = _registered("b", HookMode.GATE, HookFailureMode.FAIL_CLOSED)
    action, _, reasons = aggregate_decisions((
        _outcome(h1, HookAction.DENY, "reason_a"),
        _outcome(h2, HookAction.DENY, "reason_b"),
    ))
    assert action is HookAction.DENY
    assert reasons == ("reason_a", "reason_b")
