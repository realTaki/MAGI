"""Unit tests for the BUS hook contract surface."""

from __future__ import annotations

import dataclasses

import pytest

from magi.bus.hooks.contracts import (
    CausalityHookContext,
    HookAction,
    HookDataClassification,
    HookDataScope,
    HookDecision,
    HookEnvelope,
    HookEvaluation,
    HookEvaluationStatus,
    HookFailureMode,
    HookMode,
    HookPoint,
    HookRegistration,
    HookSubject,
    PrincipalHookContext,
    PrincipalType,
    RuntimeHookContext,
    SecurityHookContext,
)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _sample_runtime():
    return RuntimeHookContext(
        magi_id=1, magis_id=1, runtime_id="rt",
        runtime_instance_id="rt-1", environment="dev", workspace_id="ws",
    )


def _sample_principal():
    return PrincipalHookContext(
        principal_type=PrincipalType.USER,
        principal_id="u-1", role="user", permissions=("read",),
        membership_id="m-1", source_type="webui", source_id="u-1",
    )


def _sample_causality():
    return CausalityHookContext(
        correlation_id="corr", causation_id="cause",
        event_id="evt", run_id="run", conversation_id="conv",
        session_id="sess", message_id="msg", reply_to=None,
        external_event_id=None,
    )


def _sample_security():
    return SecurityHookContext(
        attempt=0, deadline=None, created_at=_now(),
        available_at=_now(), policy_labels=(),
        security_labels=(),
        data_classification=HookDataClassification.INTERNAL,
    )


def test_all_hook_points_have_string_values():
    """Every HookPoint enum member has a stable string value."""
    expected = {
        "agent.input.pending",
        "llm.request.prepared",
        "llm.response.received",
        "tool.call.pending",
        "tool.result.received",
        "a2a.invocation.pending",
        "a2a.result.received",
        "delivery.pending",
        "run.transition.committed",
        "operation.failed",
        "operation.dead_lettered",
    }
    actual = {point.value for point in HookPoint}
    assert actual == expected


def test_hook_envelope_is_immutable():
    envelope = HookEnvelope(
        schema_version="1.0.0",
        hook_event_id="hkevt_1",
        hook_point=HookPoint.AGENT_INPUT_PENDING,
        occurred_at=_now(),
        runtime=_sample_runtime(),
        principal=_sample_principal(),
        causality=_sample_causality(),
        subject=HookSubject(subject_type="agent_inbox", subject_id="evt-1"),
        payload={"text": "hi"},
        context={},
        security=_sample_security(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.hook_event_id = "tampered"  # type: ignore[misc]


def test_hook_decision_is_frozen_after_construction():
    """Once constructed, a HookDecision cannot be mutated."""
    decision = HookDecision(
        hook_id="x", hook_version="1", hook_event_id="e",
        action=HookAction.ALLOW,
    )
    assert decision.action is HookAction.ALLOW
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.action = HookAction.DENY  # type: ignore[misc]


def test_hook_mode_and_action_are_distinct():
    """HookMode and HookAction are different enums."""
    assert HookMode.GATE is not HookAction.ALLOW
    assert HookMode.GATE.value == "gate"
    assert HookAction.DENY.value == "deny"


def test_hook_data_scopes_have_distinct_values():
    """No two HookDataScope values collide."""
    values = [s.value for s in HookDataScope]
    assert len(values) == len(set(values))


def test_hook_failure_mode_default_values():
    assert HookFailureMode.FAIL_OPEN.value == "fail_open"
    assert HookFailureMode.FAIL_CLOSED.value == "fail_closed"


def test_hook_evaluation_status_terminal_values():
    """The terminal statuses (completed/failed/timed_out/errored) are distinct from pending."""
    terminal = {
        HookEvaluationStatus.COMPLETED.value,
        HookEvaluationStatus.FAILED.value,
        HookEvaluationStatus.TIMED_OUT.value,
        HookEvaluationStatus.ERRORED.value,
    }
    non_terminal = {
        HookEvaluationStatus.PENDING.value,
        HookEvaluationStatus.RUNNING.value,
    }
    assert terminal.isdisjoint(non_terminal)


def test_hook_registration_rejects_empty_hook_points():
    """An empty hook_points tuple is allowed at the type level
    but the registry rejects it (see test_registry.py); this test
    only checks the type permits the field."""
    reg = HookRegistration(
        hook_id="x", hook_version="1", hook_points=(),
        mode=HookMode.GATE, priority=0,
        required_scopes=frozenset(), timeout_ms=100,
        failure_mode=HookFailureMode.FAIL_OPEN,
    )
    assert reg.hook_points == ()


def test_hook_envelope_carries_minimal_fields():
    """The envelope carries the spec §5.3 common-data fields."""
    envelope = HookEnvelope(
        schema_version="1.0.0",
        hook_event_id="hkevt_1",
        hook_point=HookPoint.AGENT_INPUT_PENDING,
        occurred_at=_now(),
        runtime=_sample_runtime(),
        principal=_sample_principal(),
        causality=_sample_causality(),
        subject=HookSubject(subject_type="agent_inbox", subject_id="evt-1"),
        payload={},
        context={},
        security=_sample_security(),
    )
    assert envelope.runtime.magi_id == 1
    assert envelope.runtime.runtime_id == "rt"
    assert envelope.principal.principal_type is PrincipalType.USER
    assert envelope.causality.run_id == "run"
    assert envelope.security.data_classification is HookDataClassification.INTERNAL
