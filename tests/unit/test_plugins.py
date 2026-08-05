"""Unit tests for magi.plugins — new BUS hook subsystem smoke tests.

The legacy ``magi.plugins.bus`` (fire-and-forget ``HookBus``) and
``magi.plugins.samples.audit_log`` have been replaced by the
BUS-owned hook subsystem at :mod:`magi.bus.hooks` and the
plugin-side surface at :mod:`magi.plugins.hooks`.  The detailed
tests for those subsystems live under
``tests/bus/hooks/`` and ``tests/integration/hooks/``; this
file is kept as a compatibility shim so existing import paths
``from magi.plugins import …`` keep working in unrelated tests.
"""

from __future__ import annotations

import pytest

from magi.plugins import (
    HookAction,
    HookDataScope,
    HookDecision,
    HookEnvelope,
    HookEvaluation,
    HookFailureMode,
    HookHandlerProtocol,
    HookMode,
    HookPoint,
    HookRegistration,
    Plugin,
    PrincipalType,
    RuntimeHookContext,
    SecurityHookContext,
    PrincipalHookContext,
    CausalityHookContext,
    hook_handler,
)


def test_plugin_marker_protocol_exists():
    """The :class:`Plugin` marker protocol is the only back-compat export."""
    assert hasattr(Plugin, "__protocol_attrs__") or hasattr(Plugin, "name")


def test_hook_contracts_re_exported():
    assert HookAction.ALLOW.value == "allow"
    assert HookAction.DENY.value == "deny"
    assert HookMode.GATE.value == "gate"
    assert HookMode.OBSERVE.value == "observe"
    assert HookFailureMode.FAIL_OPEN.value == "fail_open"
    assert HookFailureMode.FAIL_CLOSED.value == "fail_closed"
    assert HookDataScope.LLM_REQUEST.value == "llm.request"
    assert HookPoint.AGENT_INPUT_PENDING.value == "agent.input.pending"
    assert PrincipalType.SYSTEM.value == "system"


def test_hook_handler_decorator_produces_handler():
    from magi.plugins.hooks.base import HookHandler

    @hook_handler(
        hook_id="test_plugin",
        hook_version="1.0.0",
        hook_points=(HookPoint.RUN_TRANSITION_COMMITTED,),
        mode=HookMode.OBSERVE,
        priority=0,
        required_scopes=frozenset({HookDataScope.RUNTIME_IDENTITY}),
        timeout_ms=100,
        failure_mode=HookFailureMode.FAIL_OPEN,
    )
    async def _handler(envelope):
        return None

    assert isinstance(_handler, HookHandler)
    assert _handler.registration.hook_id == "test_plugin"
    assert _handler.registration.mode is HookMode.OBSERVE


@pytest.mark.parametrize("name", [
    "HookAction", "HookDataScope", "HookDecision", "HookEnvelope",
    "HookEvaluation", "HookFailureMode", "HookHandlerProtocol",
    "HookMode", "HookPoint", "HookRegistration", "Plugin",
])
def test_back_compat_exports(name):
    """Every name in the old plugin public surface still resolves."""
    from magi.plugins import __dict__ as public
    assert name in public, f"missing back-compat export: {name}"
