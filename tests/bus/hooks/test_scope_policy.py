"""Unit tests for the HookPoint → HookDataScope policy."""

from __future__ import annotations

import pytest

from magi.bus.hooks.contracts import HookDataScope, HookPoint
from magi.bus.hooks.scope_policy import (
    HOOK_POINT_ALLOWED_SCOPES,
    allowed_scopes_for,
    scope_granted_for_registration,
)


def test_every_hook_point_has_policy_entry():
    """Adding a new HookPoint without a policy entry is a bug."""
    assert set(HOOK_POINT_ALLOWED_SCOPES.keys()) == set(HookPoint)


def test_llm_request_scope_only_at_llm_points():
    """``LLM_REQUEST`` is NOT granted at any tool / delivery point."""
    for point, scopes in HOOK_POINT_ALLOWED_SCOPES.items():
        if point is HookPoint.LLM_REQUEST_PREPARED:
            assert HookDataScope.LLM_REQUEST in scopes
        else:
            assert HookDataScope.LLM_REQUEST not in scopes, (
                f"LLM_REQUEST leaked into {point.value!r}"
            )


def test_session_window_only_at_llm_request():
    """``SESSION_WINDOW`` is granted ONLY at LLM_REQUEST_PREPARED."""
    for point, scopes in HOOK_POINT_ALLOWED_SCOPES.items():
        if point is HookPoint.LLM_REQUEST_PREPARED:
            assert HookDataScope.SESSION_WINDOW in scopes
        else:
            assert HookDataScope.SESSION_WINDOW not in scopes, (
                f"SESSION_WINDOW leaked into {point.value!r}"
            )


def test_tool_call_only_at_tool_call_points():
    """``TOOL_CALL`` is granted ONLY at TOOL_CALL_PENDING."""
    for point, scopes in HOOK_POINT_ALLOWED_SCOPES.items():
        if point is HookPoint.TOOL_CALL_PENDING:
            assert HookDataScope.TOOL_CALL in scopes
        else:
            assert HookDataScope.TOOL_CALL not in scopes


def test_a2a_invocation_only_at_a2a_invocation_point():
    for point, scopes in HOOK_POINT_ALLOWED_SCOPES.items():
        if point is HookPoint.A2A_INVOCATION_PENDING:
            assert HookDataScope.A2A_INVOCATION in scopes
        else:
            assert HookDataScope.A2A_INVOCATION not in scopes


def test_delivery_payload_only_at_delivery_point():
    for point, scopes in HOOK_POINT_ALLOWED_SCOPES.items():
        if point is HookPoint.DELIVERY_PENDING:
            assert HookDataScope.DELIVERY_PAYLOAD in scopes
        else:
            assert HookDataScope.DELIVERY_PAYLOAD not in scopes


def test_allowed_scopes_for_unknown_raises():
    """``allowed_scopes_for`` raises ``KeyError`` for unknown points
    so a missing policy entry surfaces immediately rather than
    silently granting no scopes."""
    # Simulate a missing entry by mutating the dict (test only).
    original = HOOK_POINT_ALLOWED_SCOPES.copy()
    try:
        del HOOK_POINT_ALLOWED_SCOPES[HookPoint.AGENT_INPUT_PENDING]
        with pytest.raises(KeyError):
            allowed_scopes_for(HookPoint.AGENT_INPUT_PENDING)
    finally:
        HOOK_POINT_ALLOWED_SCOPES.update(original)


def test_scope_granted_for_registration_is_union():
    """``scope_granted_for_registration`` returns the union of scopes
    permitted across every subscribed HookPoint."""
    from magi.bus.hooks.contracts import HookMode, HookFailureMode

    class _Reg:
        hook_points = (HookPoint.LLM_REQUEST_PREPARED, HookPoint.RUN_TRANSITION_COMMITTED)
        required_scopes = frozenset({HookDataScope.RUNTIME_IDENTITY, HookDataScope.LLM_REQUEST})

    granted = scope_granted_for_registration(_Reg())
    assert HookDataScope.RUNTIME_IDENTITY in granted
    assert HookDataScope.LLM_REQUEST in granted
    assert HookDataScope.PRINCIPAL_IDENTITY in granted  # from LLM point
    assert HookDataScope.RUN_STATE in granted  # from run point
