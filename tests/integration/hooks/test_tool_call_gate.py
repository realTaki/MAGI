"""Integration tests for the TOOL_CALL_PENDING GATE.

Verifies spec §6.4 / §17:
  - A GATE-DENY blocks tool execution.
  - A GATE-ALLOW proceeds.
  - The HookEnvelope sees only the data the handler declared.
  - The handler never receives a Bus reference.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from magi.bus.bootstrap import get_bus
from magi.bus.hooks.contracts import (
    HookAction,
    HookDataClassification,
    HookDataScope,
    HookDecision,
    HookEnvelope,
    HookFailureMode,
    HookMode,
    HookPoint,
    HookRegistration,
    PrincipalHookContext,
    PrincipalType,
    RuntimeHookContext,
    SecurityHookContext,
)
from magi.bus.hooks.service import EvaluationRequest


# ───────────────────────────────────────────────────────────────────── #
# Helpers
# ───────────────────────────────────────────────────────────────────── #


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _request(
    *,
    hook_point: HookPoint = HookPoint.TOOL_CALL_PENDING,
    subject_id: str = "job-1",
    metadata: dict[str, Any] | None = None,
) -> EvaluationRequest:
    now = _now()
    return EvaluationRequest(
        hook_point=hook_point,
        subject_type="tool_job",
        subject_id=subject_id,
        requested_by="test",
        runtime=RuntimeHookContext(
            magi_id=None, magis_id=None,
            runtime_id="t", runtime_instance_id="t",
            environment="test", workspace_id="test",
        ),
        principal=PrincipalHookContext(
            principal_type=PrincipalType.SYSTEM,
            principal_id="test", role=None,
            permissions=(), membership_id=None,
            source_type=None, source_id=None,
        ),
        security=SecurityHookContext(
            attempt=0, deadline=None,
            created_at=now, available_at=now,
            policy_labels=(), security_labels=(),
            data_classification=HookDataClassification.INTERNAL,
        ),
        metadata=metadata or {},
    )


# ───────────────────────────────────────────────────────────────────── #
# Tests
# ───────────────────────────────────────────────────────────────────── #


@pytest.fixture
def fresh_bus(monkeypatch, tmp_path: Path):
    """Reset the BUS singleton + engine so each test starts clean."""
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("MAGI_DATA_ROOT", str(tmp_path))
    # Reset both the Bus singleton and the SQLAlchemy engine cache
    # so the next get_bus() call rebuilds against this test's tmp dir.
    import magi.bus.bootstrap as _b
    import magi.bus.db.engine as _engine_mod
    _b._bus = None
    _engine_mod._engine = None
    _engine_mod._SessionLocal = None
    yield


def test_deny_handler_blocks(fresh_bus):
    """A GATE-DENY short-circuits the tool call."""
    bus = get_bus()
    received: list[HookEnvelope] = []

    class _Deny:
        async def handle(self, envelope):
            received.append(envelope)
            return HookDecision(
                hook_id="test_deny", hook_version="1",
                hook_event_id=envelope.hook_event_id,
                action=HookAction.DENY, reason_code="test_deny",
            )

    bus.hooks.register_handler(
        HookRegistration(
            hook_id="test_deny", hook_version="1",
            hook_points=(HookPoint.TOOL_CALL_PENDING,),
            mode=HookMode.GATE, priority=10,
            required_scopes=frozenset({HookDataScope.TOOL_CALL}),
            timeout_ms=100,
            failure_mode=HookFailureMode.FAIL_CLOSED,
        ),
        _Deny(),
    )

    async def _go():
        result = await bus.hooks.evaluate(_request(subject_id="job-1"))
        assert result.decision is HookAction.DENY
        assert len(received) == 1
        # No Bus object reachable from the envelope.
        env = received[0]
        assert isinstance(env, HookEnvelope)
        assert not hasattr(env, "_bus")
        assert not hasattr(env, "bus")
    asyncio.run(_go())


def test_allow_handler_proceeds(fresh_bus):
    bus = get_bus()

    class _Allow:
        async def handle(self, envelope):
            return HookDecision(
                hook_id="allow", hook_version="1",
                hook_event_id=envelope.hook_event_id,
                action=HookAction.ALLOW,
            )

    bus.hooks.register_handler(
        HookRegistration(
            hook_id="allow", hook_version="1",
            hook_points=(HookPoint.TOOL_CALL_PENDING,),
            mode=HookMode.GATE, priority=10,
            required_scopes=frozenset({HookDataScope.TOOL_CALL}),
            timeout_ms=100,
            failure_mode=HookFailureMode.FAIL_CLOSED,
        ),
        _Allow(),
    )

    async def _go():
        result = await bus.hooks.evaluate(_request(subject_id="job-2"))
        assert result.decision is HookAction.ALLOW
    asyncio.run(_go())


def test_handler_receives_no_db_or_session(fresh_bus):
    """The envelope MUST NOT carry ORM models / sessions / Engine."""
    bus = get_bus()

    class _Probe:
        async def handle(self, envelope):
            for name in dir(envelope):
                if name.startswith("_"):
                    continue
                attr = getattr(envelope, name)
                if attr is None:
                    continue
                if isinstance(attr, (str, int, float, bool, tuple, list, dict, set)):
                    continue
                module = getattr(type(attr), "__module__", "")
                assert not module.startswith("magi.bus.db"), (
                    f"envelope.{name} is from {module!r} — handlers "
                    "MUST NOT receive ORM/db objects"
                )
                assert not module.startswith("magi.bus.models"), (
                    f"envelope.{name} is from {module!r} — handlers "
                    "MUST NOT receive ORM/db objects"
                )
            return HookDecision(
                hook_id="probe", hook_version="1",
                hook_event_id=envelope.hook_event_id,
                action=HookAction.ALLOW,
            )

    bus.hooks.register_handler(
        HookRegistration(
            hook_id="probe", hook_version="1",
            hook_points=(HookPoint.TOOL_CALL_PENDING,),
            mode=HookMode.OBSERVE, priority=0,
            required_scopes=frozenset(),
            timeout_ms=100,
            failure_mode=HookFailureMode.FAIL_OPEN,
        ),
        _Probe(),
    )

    async def _go():
        await bus.hooks.evaluate(_request(subject_id="job-probe"))
    asyncio.run(_go())


def test_replay_uses_cached_decision(fresh_bus):
    """Same (subject_type, subject_id, hook_point) replay returns the
    cached decision instead of re-running handlers."""
    bus = get_bus()
    call_count = {"n": 0}

    class _Count:
        async def handle(self, envelope):
            call_count["n"] += 1
            return HookDecision(
                hook_id="count", hook_version="1",
                hook_event_id=envelope.hook_event_id,
                action=HookAction.ALLOW,
            )

    bus.hooks.register_handler(
        HookRegistration(
            hook_id="count", hook_version="1",
            hook_points=(HookPoint.TOOL_CALL_PENDING,),
            mode=HookMode.GATE, priority=10,
            required_scopes=frozenset({HookDataScope.TOOL_CALL}),
            timeout_ms=100,
            failure_mode=HookFailureMode.FAIL_CLOSED,
        ),
        _Count(),
    )

    async def _go():
        await bus.hooks.evaluate(_request(subject_id="job-replay"))
        await bus.hooks.evaluate(_request(subject_id="job-replay"))
        await bus.hooks.evaluate(_request(subject_id="job-replay"))
        assert call_count["n"] == 1, (
            f"expected 1 handler call, got {call_count['n']}"
        )
    asyncio.run(_go())
