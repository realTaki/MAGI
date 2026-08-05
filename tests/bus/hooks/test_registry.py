"""Unit tests for the in-memory :class:`HookRegistry`."""

from __future__ import annotations

import pytest

from magi.bus.hooks.contracts import (
    HookDataScope,
    HookEnvelope,
    HookFailureMode,
    HookHandlerProtocol,
    HookMode,
    HookPoint,
    HookRegistration,
)
from magi.bus.hooks.registry import HookRegistrationError, HookRegistry


class _EchoHandler:
    """Minimal handler implementation."""

    async def handle(self, envelope: HookEnvelope):
        return None


def _reg(
    hook_id: str = "h1",
    *,
    hook_points: tuple[HookPoint, ...] = (HookPoint.AGENT_INPUT_PENDING,),
    mode: HookMode = HookMode.GATE,
    priority: int = 10,
    required_scopes: frozenset = frozenset({HookDataScope.RUNTIME_IDENTITY}),
    timeout_ms: int = 100,
    failure_mode: HookFailureMode = HookFailureMode.FAIL_CLOSED,
    enabled: bool = True,
):
    return HookRegistration(
        hook_id=hook_id,
        hook_version="1",
        hook_points=hook_points,
        mode=mode,
        priority=priority,
        required_scopes=required_scopes,
        timeout_ms=timeout_ms,
        failure_mode=failure_mode,
        enabled=enabled,
    )


def test_register_and_unregister():
    reg = HookRegistry()
    reg.register(_reg(hook_id="a"), _EchoHandler())
    reg.register(_reg(hook_id="b"), _EchoHandler())
    assert reg.get("a") is not None
    assert reg.get("b") is not None
    reg.unregister("a")
    assert reg.get("a") is None


def test_rejects_zero_timeout():
    reg = HookRegistry()
    with pytest.raises(HookRegistrationError):
        reg.register(_reg(timeout_ms=0), _EchoHandler())


def test_rejects_empty_hook_points():
    reg = HookRegistry()
    with pytest.raises(HookRegistrationError):
        reg.register(_reg(hook_points=()), _EchoHandler())


def test_rejects_out_of_scope_request():
    reg = HookRegistry()
    bad = _reg(
        hook_points=(HookPoint.TOOL_CALL_PENDING,),
        required_scopes=frozenset({HookDataScope.LLM_REQUEST}),
    )
    with pytest.raises(HookRegistrationError):
        reg.register(bad, _EchoHandler())


def test_handlers_for_filters_by_point_and_enabled():
    reg = HookRegistry()
    reg.register(
        _reg(hook_id="on", hook_points=(HookPoint.AGENT_INPUT_PENDING,)),
        _EchoHandler(),
    )
    reg.register(
        _reg(
            hook_id="off", enabled=False,
            hook_points=(HookPoint.AGENT_INPUT_PENDING,),
        ),
        _EchoHandler(),
    )
    reg.register(
        _reg(hook_id="other", hook_points=(HookPoint.TOOL_CALL_PENDING,)),
        _EchoHandler(),
    )
    handlers = reg.handlers_for(HookPoint.AGENT_INPUT_PENDING)
    assert len(handlers) == 1
    assert handlers[0].registration.hook_id == "on"


def test_handlers_sorted_by_priority():
    reg = HookRegistry()
    reg.register(
        _reg(hook_id="p10", priority=10), _EchoHandler(),
    )
    reg.register(
        _reg(hook_id="p5", priority=5), _EchoHandler(),
    )
    reg.register(
        _reg(hook_id="p20", priority=20), _EchoHandler(),
    )
    handlers = reg.handlers_for(HookPoint.AGENT_INPUT_PENDING)
    assert [h.registration.hook_id for h in handlers] == ["p5", "p10", "p20"]


def test_gate_handlers_ordered_before_observe_at_same_priority():
    reg = HookRegistry()
    reg.register(
        _reg(
            hook_id="ob", priority=5,
            mode=HookMode.OBSERVE,
            failure_mode=HookFailureMode.FAIL_OPEN,
        ),
        _EchoHandler(),
    )
    reg.register(
        _reg(
            hook_id="gt", priority=5,
            mode=HookMode.GATE,
            failure_mode=HookFailureMode.FAIL_CLOSED,
        ),
        _EchoHandler(),
    )
    handlers = reg.handlers_for(HookPoint.AGENT_INPUT_PENDING)
    assert [h.registration.hook_id for h in handlers] == ["gt", "ob"]


def test_disable_then_enable_round_trip():
    reg = HookRegistry()
    reg.register(_reg(hook_id="x"), _EchoHandler())
    reg.disable("x")
    assert reg.handlers_for(HookPoint.AGENT_INPUT_PENDING) == ()
    reg.enable("x")
    assert len(reg.handlers_for(HookPoint.AGENT_INPUT_PENDING)) == 1


def test_projected_scopes_equals_required_scopes():
    reg = HookRegistry()
    scopes = frozenset({HookDataScope.RUNTIME_IDENTITY, HookDataScope.LLM_REQUEST})
    reg.register(
        _reg(
            hook_points=(HookPoint.LLM_REQUEST_PREPARED,),
            required_scopes=scopes,
        ),
        _EchoHandler(),
    )
    registered = reg.get("h1")
    assert registered is not None
    assert registered.projected_scopes == scopes
