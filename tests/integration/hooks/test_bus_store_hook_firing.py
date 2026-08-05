"""Integration tests for BUS hook firing from bus.store boundary methods.

The principle under test: hooks fire ONLY from bus.store boundary
methods (``enqueue_*`` / ``complete_*``).  Business modules
(provider/agent/tools/channels) never call HookService directly.

Each test exercises one boundary method and verifies the hook
fired by checking the audit log on the hook service.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from magi.bus.bootstrap import get_bus
from magi.bus.hooks.contracts import (
    HookContext,
    HookDataClassification,
    HookDataScope,
    HookDecision,
    HookFailureMode,
    HookMode,
    HookPoint,
    HookRegistration,
    PrincipalType,
)
from magi.bus.hooks.service import HookService


def _user_hook_context(*, requested_by: str = "test") -> HookContext:
    return HookContext(
        requested_by=requested_by,
        principal_type=PrincipalType.USER,
        principal_id="test-user",
        role=None,
        source_type="test",
        source_id="test-user",
        session_id="test-session",
        run_id="test-run",
        event_id="test-event",
        data_classification=HookDataClassification.INTERNAL,
    )


@pytest.fixture
def fresh_bus(monkeypatch, tmp_path: Path):
    """Reset the BUS singleton + engine so each test starts clean."""
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("MAGI_DATA_ROOT", str(tmp_path))
    _bm = importlib.import_module("magi.bus.bootstrap")
    import magi.bus.db.engine as _engine_mod
    _bm._bus = None
    _engine_mod._engine = None
    yield


def _register_capture_hook(
    *,
    hook_point: HookPoint,
    captured: list,
    mode: HookMode = HookMode.GATE,
    decision: HookDecision | None = None,
) -> None:
    """Install a single hook that records every call onto ``captured``."""
    bus = get_bus()
    if not hasattr(bus, "hooks") or bus.hooks is None:
        return
    handler = _RecordHandler(captured)
    scopes = {
        HookPoint.LLM_REQUEST_PREPARED: {
            HookDataScope.RUNTIME_IDENTITY,
            HookDataScope.PRINCIPAL_IDENTITY,
            HookDataScope.CAUSALITY,
            HookDataScope.LLM_REQUEST,
        },
        HookPoint.LLM_RESPONSE_RECEIVED: {
            HookDataScope.RUNTIME_IDENTITY,
            HookDataScope.PRINCIPAL_IDENTITY,
            HookDataScope.CAUSALITY,
            HookDataScope.LLM_RESPONSE,
        },
        HookPoint.TOOL_CALL_PENDING: {
            HookDataScope.RUNTIME_IDENTITY,
            HookDataScope.PRINCIPAL_IDENTITY,
            HookDataScope.CAUSALITY,
            HookDataScope.TOOL_CALL,
        },
        HookPoint.TOOL_RESULT_RECEIVED: {
            HookDataScope.RUNTIME_IDENTITY,
            HookDataScope.CAUSALITY,
            HookDataScope.TOOL_RESULT,
        },
        HookPoint.DELIVERY_PENDING: {
            HookDataScope.RUNTIME_IDENTITY,
            HookDataScope.PRINCIPAL_IDENTITY,
            HookDataScope.CAUSALITY,
            HookDataScope.DELIVERY_PAYLOAD,
        },
        HookPoint.DELIVERY_DISPATCHED: {
            HookDataScope.RUNTIME_IDENTITY,
            HookDataScope.CAUSALITY,
        },
    }[hook_point]
    registration = HookRegistration(
        hook_id=f"capture-{hook_point.value}",
        hook_version="1",
        hook_points=(hook_point,),
        mode=mode,
        priority=0,
        required_scopes=frozenset(scopes),
        timeout_ms=1000,
        failure_mode=HookFailureMode.FAIL_OPEN,
    )
    if decision is not None:
        bus.hooks.register(registration, _fixed_handler(handler, decision))
    else:
        bus.hooks.register(registration, handler)


def _fixed_handler(fallback, decision: HookDecision):
    """Wrap a handler so it always returns the same decision."""
    async def _wrapped(envelope):
        fallback(envelope)
        return decision
    return _wrapped


class _RecordHandler:
    """Async handler that records every envelope it sees."""

    def __init__(self, captured: list) -> None:
        self._captured = captured

    async def handle(self, envelope) -> None:
        self._captured.append(envelope)


# ───────────────────────────────────────────────────────────────────── #
# Tests
# ───────────────────────────────────────────────────────────────────── #


def test_bus_store_enqueue_tool_job_fires_tool_call_pending_gate(
    fresh_bus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bus.store.enqueue_tool_job fires the TOOL_CALL_PENDING GATE."""
    bus = get_bus()
    captured_envelopes: list = []
    _register_capture_hook(hook_point=HookPoint.TOOL_CALL_PENDING, captured=captured_envelopes)

    store = bus.store
    result = store.enqueue_tool_job(
        run_id="test-run",
        tool_call_id="call-1",
        tool_name="echo",
        arguments={"text": "hi"},
        context={"uid": 1, "channel": "test", "session_id": "test-session"},
        hook_context=_user_hook_context(requested_by="agent.commit"),
    )
    assert result.row_id  # row was created
    assert len(captured_envelopes) == 1
    envelope = captured_envelopes[0]
    assert envelope.hook_point == HookPoint.TOOL_CALL_PENDING


def test_bus_store_complete_tool_job_fires_tool_result_observation(
    fresh_bus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bus.store.complete_tool_job fires the TOOL_RESULT_RECEIVED OBSERVE."""
    bus = get_bus()
    store = bus.store
    # First enqueue so the job row exists.
    enqueue = store.enqueue_tool_job(
        run_id="test-run",
        tool_call_id="call-2",
        tool_name="echo",
        arguments={"text": "hi"},
        context={"uid": 1, "channel": "test"},
    )
    from magi.bus.protocols.tools import ToolClaim
    claim = ToolClaim(
        job_id=enqueue.row_id,
        run_id="test-run",
        tool_call_id="call-2",
        tool_name="echo",
        payload={"arguments": {"text": "hi"}, "context": {"uid": 1}},
        attempts=1,
    )
    captured: list = []
    _register_capture_hook(
        hook_point=HookPoint.TOOL_RESULT_RECEIVED,
        captured=captured,
        mode=HookMode.OBSERVE,
    )
    store.complete_tool_job(
        claim,
        content="hi back",
        is_error=False,
        hook_context=_user_hook_context(requested_by="tools.worker"),
    )
    assert len(captured) == 1
    assert captured[0].hook_point == HookPoint.TOOL_RESULT_RECEIVED


def test_bus_store_enqueue_delivery_fires_delivery_pending_gate(
    fresh_bus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bus.store.enqueue_delivery fires the DELIVERY_PENDING GATE."""
    bus = get_bus()
    captured: list = []
    _register_capture_hook(
        hook_point=HookPoint.DELIVERY_PENDING,
        captured=captured,
        mode=HookMode.GATE,
        decision=HookDecision(
            hook_id="fixed",
            hook_version="1",
            hook_event_id="x",
            action=__import__(
                "magi.bus.hooks.contracts", fromlist=["HookAction"]
            ).HookAction.ALLOW,
        ),
    )
    store = bus.store
    result = store.enqueue_delivery(
        channel="tg",
        destination="@target",
        payload={"text": "hello"},
        run_id="test-run",
        hook_context=_user_hook_context(requested_by="channels.dispatcher"),
    )
    assert result.row_id  # delivery was created
    assert len(captured) == 1
    assert captured[0].hook_point == HookPoint.DELIVERY_PENDING


def test_bus_store_complete_delivery_fires_delivery_dispatched_observation(
    fresh_bus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bus.store.complete_delivery fires the DELIVERY_DISPATCHED OBSERVE."""
    bus = get_bus()
    store = bus.store
    # Enqueue without a hook (no audit noise), then complete with one.
    enq = store.enqueue_delivery(
        channel="tg", destination="@target", payload={"text": "x"},
    )
    delivery_id = getattr(enq, "row_id", enq)
    captured: list = []
    _register_capture_hook(
        hook_point=HookPoint.DELIVERY_DISPATCHED,
        captured=captured,
        mode=HookMode.OBSERVE,
    )
    store.complete_delivery(
        delivery_id,
        hook_context=_user_hook_context(requested_by="delivery.worker"),
    )
    assert len(captured) == 1
    assert captured[0].hook_point == HookPoint.DELIVERY_DISPATCHED


def test_bus_store_enqueue_llm_job_fires_llm_request_prepared_gate(
    fresh_bus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bus.store.enqueue_llm_job fires the LLM_REQUEST_PREPARED GATE."""
    bus = get_bus()
    captured: list = []
    _register_capture_hook(
        hook_point=HookPoint.LLM_REQUEST_PREPARED,
        captured=captured,
        mode=HookMode.GATE,
        decision=HookDecision(
            hook_id="fixed",
            hook_version="1",
            hook_event_id="x",
            action=__import__(
                "magi.bus.hooks.contracts", fromlist=["HookAction"]
            ).HookAction.ALLOW,
        ),
    )
    store = bus.store
    result = store.enqueue_llm_job(
        run_id="test-run",
        request={"system": "s", "messages": [{"role": "user", "content": "hi"}]},
        inbox_event_id="evt-1",
        kind="chat",
        hook_context=_user_hook_context(requested_by="agent.worker"),
    )
    assert result.row_id
    assert len(captured) == 1
    assert captured[0].hook_point == HookPoint.LLM_REQUEST_PREPARED


def test_bus_store_complete_llm_attempt_fires_llm_response_observation(
    fresh_bus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bus.store.complete_llm_attempt fires the LLM_RESPONSE_RECEIVED OBSERVE."""
    bus = get_bus()
    store = bus.store
    enq = store.enqueue_llm_job(
        run_id="test-run",
        request={"messages": [{"role": "user", "content": "hi"}]},
        inbox_event_id="evt-1",
        kind="chat",
    )
    attempt_id = getattr(enq, "row_id", enq)
    captured: list = []
    _register_capture_hook(
        hook_point=HookPoint.LLM_RESPONSE_RECEIVED,
        captured=captured,
        mode=HookMode.OBSERVE,
    )
    store.complete_llm_attempt(
        attempt_id,
        response={"text": "hello", "tool_uses": []},
        hook_context=_user_hook_context(requested_by="provider.worker"),
    )
    assert len(captured) == 1
    assert captured[0].hook_point == HookPoint.LLM_RESPONSE_RECEIVED
